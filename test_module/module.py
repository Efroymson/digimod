# module.py — top of file
import socket
import struct
import threading
import queue
import time
import logging
import tkinter as tk
from tkinter import ttk
from typing import Dict, Any, Optional
from enum import Enum, auto

# ←←← Import the pure logic (safe — no circular import at runtime)
from connection_protocol import (
    InputJack, OutputJack, InputState, OutputState,
    ProtocolMessage, ProtocolMessageType,
    LedState, ConnectionRecord,
    CONTROL_MULTICAST, UDP_CONTROL_PORT, UDP_AUDIO_PORT
)

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# JackWidget — also moved here (tiny Tkinter widget)
# ------------------------------------------------------------------
class JackWidget(tk.Frame):
    def __init__(self, parent, io_id, label,
                 short_press_callback=None,
                 long_press_callback=None,
                 verbose_text=True,
                 is_output=False):
        super().__init__(parent, width=80, height=80, bg="gray20", relief="raised", bd=2)
        self.io_id = io_id
        self.short_press_callback = short_press_callback
        self.long_press_callback = long_press_callback
        self.root = parent.winfo_toplevel()
        self.is_output = is_output

        self.canvas = tk.Canvas(self, width=60, height=60, bg="gray20", highlightthickness=0)
        self.canvas.pack(pady=5)
        self.rect = self.canvas.create_rectangle(10, 10, 50, 50, fill="gray50", outline="white", width=2)

        if verbose_text:
            tk.Label(self, text=label, fg="white", bg="gray20", font=("Helvetica", 9)).pack()

        self.press_time = 0
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)

    def _on_press(self, event):
        self.press_time = time.time()

    def _on_release(self, event):
        duration = time.time() - self.press_time
        if duration > 0.5 and self.long_press_callback:
            self.long_press_callback()
        elif self.short_press_callback:
            self.short_press_callback()

    def set_state(self, state: LedState):
        colors = {
            LedState.OFF:         "gray30",
            LedState.SOLID:       "#00ff00" if self.is_output else "#ff0000",
            LedState.BLINK_SLOW:  "#00ff00" if self.is_output else "#ff0000",
            LedState.BLINK_RAPID: "#00ff00" if self.is_output else "#ff0000",
        }
        fill_color = colors[state]

        if state in (LedState.BLINK_SLOW, LedState.BLINK_RAPID):
            # existing blink logic
            self.current_state = state
            self.blink_on = True
            self._blink()
        else:
            self.canvas.itemconfig(self.rect, fill=fill_color)
            self.current_state = state
            
    def _blink(self):
        if self.current_state not in (LedState.BLINK_SLOW, LedState.BLINK_RAPID):
            return

        # ONE SOURCE OF TRUTH — define delay first
        if self.current_state == LedState.BLINK_SLOW:
            delay = 600    # calm, visible pulse
        else:  # BLINK_RAPID
            delay = 180    # fast but not "thin" — feels solid

        # Debug print — now safe
        print(f"BLINK {self.io_id} | state={self.current_state.name} | on={self.blink_on} | delay={delay}ms")

        # Toggle
        self.blink_on = not self.blink_on

        base_color = "#00ff00" if self.is_output else "#ff0000"
        fill_color = base_color if self.blink_on else "gray30"
        self.canvas.itemconfig(self.rect, fill=fill_color)

        # Schedule next
        self.root.after(delay, self._blink)
        

def derive_mcast_group(unicast_ip: str) -> str:
    parts = unicast_ip.split('.')
    return f"239.100.{int(parts[2]):d}.{int(parts[3]):d}" if len(parts) == 4 else "239.100.0.1"

class KnobSlider:
    def __init__(self, id: str, range: tuple, var):
        self.id = id
        self.range = range
        self.var = var
        self.saved_value = var.get()
        def sync(*_):
            self.saved_value = var.get()
        var.trace_add("write", sync)

    def restore(self, value: float):
        lo, hi = self.range
        clamped = max(lo, min(hi, float(value)))
        self.var.set(clamped)
        self.saved_value = clamped

class Module:
    _next_instance_id = 100  # starts at 127.0.0.100
    def __init__(self, mod_id: str, mod_type: str, unicast_ip: str = None):
        print(f"Module.__init__ called for {mod_id} type={mod_type} ip={unicast_ip}")  # ← ADD THIS
        if unicast_ip is None:
             # Auto-assign unique loopback IP
             self.unicast_ip = f"127.0.0.{Module._next_instance_id}"
             Module._next_instance_id += 1
             if Module._next_instance_id > 200:
                 raise RuntimeError("Too many modules — out of loopback IPs!")
        else:
             self.unicast_ip = unicast_ip
        self.module_id = mod_id
        self.type = mod_type
        
        self.mcast_group = derive_mcast_group(self.unicast_ip)

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        self.sock.bind(('', UDP_CONTROL_PORT))
        mreq = struct.pack("4sl", socket.inet_aton(CONTROL_MULTICAST), socket.INADDR_ANY)
        self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)  # ← Critical!
        self.sock.settimeout(0.1)
        
        # Audio/CV receive socket — FIXED for multicast/loopback
        self.audio_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.audio_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            self.audio_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        self.audio_socket.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)  # ← ADD: Critical for loopback!
        self.audio_socket.bind(('', 5005))  # ← FIXED: Bind to all interfaces (was sometimes unicast_ip)
        self.audio_socket.settimeout(0.01)
           
       
        self.outputs = {}
        self.input_jacks = {}
        self.output_jacks = {}
        self.input_connections = {}

        self.gui_queue = queue.Queue()
        self.gui_leds = {}
        self.root = None
        self.last_push_time = {}

        self.knob_sliders = {}

        self._listener_thread = threading.Thread(target=self._listen, daemon=True)
        self._listener_thread.start()
    
    # new init helpers
    def add_input(self, io_id: str, type_: str, group: str = None):
        self.inputs[io_id] = {"type": type_, "group": group or self.mcast_group}

    def add_output(self, io_id: str, type_: str, group: str = None):
        self.outputs[io_id] = {"type": type_, "group": group or self.mcast_group}
        
    def _init_jacks(self):
        """Create InputJack/OutputJack instances from self.inputs/self.outputs"""
        if hasattr(self, "_jacks_initialized"):
            return
        self._jacks_initialized = True

        for io_id, info in self.inputs.items():
            self.input_jacks[io_id] = InputJack(io_id, self)

        for io_id, info in self.outputs.items():
            self.output_jacks[io_id] = OutputJack(io_id, self)
            # OutputJack.__init__ already calls _set_led()

        logger.debug(f"[{self.module_id}] Jacks initialized: {list(self.input_jacks)} in, {list(self.output_jacks)} out")

    # ===================================================================
    # Public send methods — ONLY these touch the socket
    # ===================================================================
    def send_initiate(self, io_id: str):
        if io_id not in self.outputs:
            return
        info = self.outputs[io_id]
        payload = {
            "group": info.get("group", self.mcast_group),
            "type": info.get("type", "unknown"),
            "offset": 0,
            "block_size": 96
        }
        msg = ProtocolMessage(ProtocolMessageType.INITIATE.value, self.module_id, self.type, io_id, payload)
        self.sock.sendto(msg.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
        logger.info(f"[{self.module_id}] INITIATE → {io_id}")
        
    # ------------------------------------------------------------------
    # Public send helpers — called from InputJack/OutputJack
    # ------------------------------------------------------------------
    def send_initiate(self, io_id: str):
        if io_id not in self.outputs:
            return
        info = self.outputs[io_id]
        payload = {
            "group": info.get("group", self.mcast_group),
            "type": info.get("type", "unknown"),
            "offset": 0,
            "block_size": 96
        }
        msg = ProtocolMessage(
            ProtocolMessageType.INITIATE.value,
            self.module_id,
            self.type,
            io_id,
            payload
        )
        self.sock.sendto(msg.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
        logger.info(f"[{self.module_id}] INITIATE sent from {io_id}")

    def send_cancel(self, io_id: str = ""):
        msg = ProtocolMessage(ProtocolMessageType.CANCEL.value, self.module_id, io_id=io_id)
        self.sock.sendto(msg.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
        logger.info(f"[{self.module_id}] CANCEL sent (io: {io_id or 'all'})")

    def send_compatible(self, io_id: str):
        if io_id not in self.inputs:
            return
        info = self.inputs[io_id]
        payload = {"type": info.get("type", "unknown")}
        msg = ProtocolMessage(
            ProtocolMessageType.COMPATIBLE.value,
            self.module_id,
            self.type,
            io_id,
            payload
        )
        self.sock.sendto(msg.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
        logger.info(f"[{self.module_id}] COMPATIBLE sent from input {io_id} type={info.get('type')}")

    def send_show_connected(self, io_id: str, target_mod: str, target_io: str):
        payload = {
            "target_mod": target_mod,
            "target_io": target_io
        }
        msg = ProtocolMessage(
            ProtocolMessageType.SHOW_CONNECTED.value,
            self.module_id,
            io_id=io_id,
            payload=payload
        )
        logger.info(f"[{self.module_id}] →→→ BROADCASTING SHOW_CONNECTED for {target_mod}:{target_io}")
        self.sock.sendto(msg.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
        
        
    def _notify_self_compatible(self, input_io_id: str):
        msg = ProtocolMessage(
            ProtocolMessageType.COMPATIBLE.value,
            self.module_id,
            self.type,
            input_io_id,
            {"type": self.inputs[input_io_id]["type"]}
        )
        self.sock.sendto(msg.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
        logger.info(f"[{self.module_id}] COMPATIBLE sent from input {input_io_id}")

    # ===================================================================
    # Save / Restore
    # ===================================================================
    def iterate_for_save(self) -> Dict:
        controls = {k: v.saved_value for k, v in self.knob_sliders.items()}
        connections = {}
        for io, rec in self.input_connections.items():
            if rec:
                connections[io] = {
                    "src": rec.src,
                    "group": rec.mcast_group,
                    "offset": rec.block_offset,
                    "block_size": rec.block_size,
                }
        return {"controls": controls, "connections": connections}

    def iterate_for_restore(self, data: Dict):
        # 1. Restore controls
        for ctrl_id, value in data.get("controls", {}).items():
            if ctrl_id in self.knob_sliders:
                self.knob_sliders[ctrl_id].restore(value)

        # 2. Wipe all connections and receivers
        for io in self.inputs:
            if io in self.input_connections:
                if hasattr(self, "_stop_receiver"):
                    self._stop_receiver(io)
                self.input_connections[io] = None

        # 3. Re-apply saved connections
        for io, info in data.get("connections", {}).items():
            if io not in self.inputs or not info:
                continue

            rec = ConnectionRecord(
                src=info["src"],
                src_io="",
                mcast_group=info["group"],
                block_offset=info.get("offset", 0),
                block_size=info.get("block_size", 96),
            )
            self.input_connections[io] = rec

            if hasattr(self, "_start_receiver"):
                self._start_receiver(io, rec.mcast_group, rec.block_offset, rec.block_size)

        # CRITICAL: Set visual state AFTER all connections are applied
        for io, rec in self.input_connections.items():
            if rec and io in self.input_jacks:
                self.input_jacks[io].state = InputState.IIdleConnected
                self._queue_led_update(io, LedState.BLINK_RAPID)
            elif io in self.input_jacks:
                self.input_jacks[io].state = InputState.IIdleDisconnected
                self._queue_led_update(io, LedState.OFF)

        # Force outputs to OIdle
        for jack in self.output_jacks.values():
            jack.state = OutputState.OIdle
            jack._set_led()

        self.refresh_all_gui()
        if self.root:
            self.root.update_idletasks() 
            
    def refresh_all_gui(self):
        for jack in self.input_jacks.values():
            jack._set_led()
        for jack in self.output_jacks.values():
            jack._set_led()

    # ===================================================================
    # GUI + Message Loop
    # ===================================================================
    def set_root(self, root):
        self.root = root
        if root:
            root.after(16, self._periodic_drain)

    def _periodic_drain(self):
        if self.root and self.root.winfo_exists():
            self._update_display()
            self.root.after(16, self._periodic_drain)

    def _update_display(self):
        try:
            while True:
                io, state = self.gui_queue.get_nowait()
                if io in self.gui_leds:
                    self.gui_leds[io].set_state(LedState[state])
        except queue.Empty:
            pass

    def _queue_led_update(self, io: str, state: LedState):
        now = time.time()
        if io in self.last_push_time and now - self.last_push_time[io] < 0.08:
            return
        self.last_push_time[io] = now
        try:
            self.gui_queue.put_nowait((io, state.name))
        except queue.Full:
            pass

    def _listen(self):
        while True:
            try:
                data, _ = self.sock.recvfrom(4096)
                msg = ProtocolMessage.unpack(data)
                threading.Thread(target=self.handle_incoming_msg, args=(msg,), daemon=True).start()
            except socket.timeout:
                continue
            except Exception as e:
                logger.debug(f"[{self.module_id}] recv error: {e}")

    def get_capabilities(self) -> dict:
        """Return everything the control panel needs to know about this module."""
        return {
            "module_id": self.module_id,
            "module_type": self.type,
            "unicast_ip": self.unicast_ip,           # ← critical for unicast restore
            "inputs": self.inputs,
            "outputs": self.outputs,
            "controls": list(self.knob_sliders.keys()),
            # optional but nice:
            "mcast_group": self.mcast_group,
            "firmware": "1.0",  # or whatever version you want
        }
    def get_state(self) -> dict:
        connections = {}
        for io_id, conn in self.input_connections.items():
            if conn:
                connections[io_id] = {
                    "src_mod": conn.src,
                    "src_io": conn.src_io,
                    "mcast_group": conn.mcast_group,
                    "offset": conn.block_offset,
                    "block_size": conn.block_size,
                }
            else:
                connections[io_id] = None

        return {
            "module_id": self.module_id,
            "module_type": self.type,
            "unicast_ip": self.unicast_ip,
            "controls": {
                name: slider.var.get()
                for name, slider in self.knob_sliders.items()
            },
            "connections": connections,
        }
        
    def restore_state(self, state: dict):
        # Restore controls
        for name, value in state.get("controls", {}).items():
            if name in self.knob_sliders:
                self.knob_sliders[name].restore(value)

        # Restore connections
        for io_id, conn_info in state.get("connections", {}).items():
            if not conn_info or io_id not in self.input_jacks:
                continue

            # Re-create the connection exactly as if user clicked
            jack = self.input_jacks[io_id]
            jack.connected_src = conn_info["src_mod"]
            jack.connected_src_io = conn_info["src_io"]
            jack.connected_mcast = conn_info["mcast_group"]
            jack.connected_offset = conn_info["offset"]
            jack.connected_block_size = conn_info["block_size"]

            # Start receiving
            self._start_receiver(
                io_id,
                conn_info["mcast_group"],
                conn_info["offset"],
                conn_info["block_size"]
            )

            # Update LED
            jack.state = InputState.IIdleConnected
            jack._set_led()
            
    def handle_incoming_msg(self, msg: ProtocolMessage):
        logger.debug(f"[{self.module_id}] Handling {ProtocolMessageType(msg.type).name} from {msg.module_id}:{msg.io_id}")
    
        # Existing jack iterations for INITIATE/CANCEL/COMPATIBLE...
        for jack in list(self.input_jacks.values()) + list(self.output_jacks.values()):
            if msg.type == ProtocolMessageType.INITIATE.value:
                jack.on_initiate(msg)
            elif msg.type == ProtocolMessageType.CANCEL.value:
                jack.on_cancel(msg)
            elif msg.type == ProtocolMessageType.COMPATIBLE.value:
                if isinstance(jack, OutputJack):
                    jack.on_compatible(msg)

        if msg.type == ProtocolMessageType.SHOW_CONNECTED.value:
            logger.info(
                f"[{self.module_id}] ←←← RECEIVED SHOW_CONNECTED from {msg.module_id}:{msg.io_id} "
                f"targeting {msg.payload.get('target_mod')}:{msg.payload.get('target_io')}"
            )
            for jack in self.output_jacks.values():
                jack.on_show_connected(msg)


        if msg.type == ProtocolMessageType.STATE_INQUIRY.value and msg.module_id == "mcu":
                state = self.get_state()  # your existing method
                resp = ProtocolMessage(
                    ProtocolMessageType.STATE_RESPONSE.value,
                    self.module_id,
                    payload=state
                )
                self.sock.sendto(resp.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
                logger.info(f"[{self.module_id}] Sent STATE_RESPONSE")
        
        elif msg.type == ProtocolMessageType.PATCH_RESTORE.value:
            payload = msg.payload
            if not isinstance(payload, dict):
                return
            state = payload.get("state", payload)
            if not isinstance(state, dict):
                return

            # 1. Restore controls
            for name, value in state.get("controls", {}).items():
                if name in self.knob_sliders:
                    self.knob_sliders[name].restore(value)

            # 2. Restore connections
            for io_id, conn_info in state.get("connections", {}).items():
                if not conn_info:
                    # Disconnected
                    if io_id in self.input_jacks:
                        self.input_jacks[io_id].state = InputState.IIdleDisconnected
                        self.input_jacks[io_id]._set_led()
                        if io_id in self.input_connections:
                            self._stop_receiver(io_id)
                            self.input_connections[io_id] = None
                    continue

                if io_id not in self.input_jacks:
                    continue

                jack = self.input_jacks[io_id]
                jack.connected_src        = conn_info["src_mod"]
                jack.connected_src_io     = conn_info["src_io"]
                jack.connected_mcast      = conn_info["mcast_group"]
                jack.connected_offset     = conn_info.get("offset", 0)
                jack.connected_block_size = conn_info.get("block_size", 96)

                self._start_receiver(io_id,
                                   conn_info["mcast_group"],
                                   conn_info.get("offset", 0),
                                   conn_info.get("block_size", 96))

                self.input_connections[io_id] = ConnectionRecord(**conn_info)

                jack.state = InputState.IIdleConnected
                jack._set_led()

            logger.info(f"[{self.module_id}] Patch restored via unicast")
            

        if msg.module_id == "mcu":
            if msg.type == ProtocolMessageType.STATE_INQUIRY.value:
                state = self.iterate_for_save()
                resp = ProtocolMessage(ProtocolMessageType.STATE_RESPONSE.value, self.module_id, payload=state)
                self.sock.sendto(resp.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))

    def _audio_receive_loop(self):
        while True:
            try:
                data, _ = self.audio_socket.recvfrom(4096)
                # Demux to correct input based on source multicast group
                # Subclasses override _handle_audio_packet(data) if needed
                if hasattr(self, "_handle_audio_packet"):
                    self._handle_audio_packet(data)
            except socket.timeout:
                continue
            except Exception as e:
                if hasattr(self, "_audio_thread"):  # still alive
                    logger.debug(f"[{self.module_id}] Audio recv error: {e}")

    def _start_receiver(self, io_id: str, group: str, offset: int = 0, block_size: int = 96):
            try:
                mreq = struct.pack("4sl", socket.inet_aton(group), socket.INADDR_ANY)  # ← VERIFIED FIX
                self.audio_socket.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
                logger.debug(f"[{self.module_id}] Joined {group} for {io_id}")
            except Exception as e:
                logger.warning(f"[{self.module_id}] Join failed {group}: {e}")

            self.input_connections[io_id] = ConnectionRecord(
                src="", src_io="", mcast_group=group,
                block_offset=offset, block_size=block_size
            )

    def _stop_receiver(self, io_id: str):
            rec = self.input_connections.get(io_id)
            if rec and rec.mcast_group:
                try:
                    mreq = struct.pack("4sl", socket.inet_aton(rec.mcast_group), socket.INADDR_ANY)  # ← VERIFIED FIX
                    self.audio_socket.setsockopt(socket.IPPROTO_IP, socket.IP_DROP_MEMBERSHIP, mreq)
                    logger.debug(f"[{self.module_id}] Dropped {rec.mcast_group}")
                except Exception as e:
                    logger.debug(f"[{self.module_id}] Drop failed: {e}")
            self.input_connections[io_id] = None
        
    def on_closing(self):
        try:
            self.sock.close()  # perhaps rename this to protocol_sock
            self.audio_socket.close() # perhaps rename this to audio_cv_sock
        except:
            pass
        if self.root:
            self.root.destroy()