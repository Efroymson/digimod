# Save as: module.py  (in the same folder as osc_module.py, etc.)

import socket
import struct
import threading
import queue
import time
import logging
from typing import Dict, Any, Optional
from base_module import (
    ProtocolMessage, ProtocolMessageType, CONTROL_MULTICAST, UDP_CONTROL_PORT,
    LedState, ConnectionRecord, JackWidget
)
from connection_protocol import InputJack, OutputJack, InputState, OutputState  

logger = logging.getLogger(__name__)

def derive_mcast_group(unicast_ip: str) -> str:
    parts = unicast_ip.split('.')
    return f"239.100.{int(parts[2]):d}.{int(parts[3]):d}" if len(parts) == 4 else "239.100.0.1"

class KnobSlider:
    def __init__(self, id: str, range: tuple, var):
        self.id = id
        self.range = range
        self.var = var
        self.saved_value = var.get()
    def restore(self, value: float):
        lo, hi = self.range
        clamped = max(lo, min(hi, float(value)))
        self.var.set(clamped)
        self.saved_value = clamped

class Module:
    def __init__(self, mod_id: str, mod_type: str, unicast_ip: str = "192.168.1.100"):
        self.module_id = mod_id
        self.type = mod_type
        self.unicast_ip = unicast_ip
        self.mcast_group = derive_mcast_group(unicast_ip)

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        self.sock.bind(('', UDP_CONTROL_PORT))
        mreq = struct.pack("4sl", socket.inet_aton(CONTROL_MULTICAST), socket.INADDR_ANY)
        self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        self.sock.settimeout(0.1)

        self.inputs: Dict[str, Dict] = {}
        self.outputs: Dict[str, Dict] = {}
        self.input_jacks: Dict[str, InputJack] = {}
        self.output_jacks: Dict[str, OutputJack] = {}
        self.input_connections: Dict[str, Optional[ConnectionRecord]] = {}
        self.knob_sliders: Dict[str, KnobSlider] = {}

        self.gui_queue = queue.Queue()
        self.gui_leds: Dict[str, JackWidget] = {}
        self.root = None
        self.last_push_time: Dict[str, float] = {}

        self._listener_thread = threading.Thread(target=self._listen, daemon=True)
        self._listener_thread.start()

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
                io, state_name = self.gui_queue.get_nowait()
                if io in self.gui_leds:
                    self.gui_leds[io].update_led(LedState[state_name])
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
        for ctrl_id, value in data.get("controls", {}).items():
            if ctrl_id in self.knob_sliders:
                self.knob_sliders[ctrl_id].restore(value)

        # ---- wipe connections and reset jack states ----
        for io in self.inputs:
            self.input_connections[io] = None
            if io in self.input_jacks:
                self.input_jacks[io].state = InputState.IIdleDisconnected
                self._queue_led_update(io, LedState.OFF)

        for io, info in data.get("connections", {}).items():
            if io not in self.inputs or not info: continue
            rec = ConnectionRecord(
                src=info["src"], src_io="", mcast_group=info["group"],
                block_offset=info.get("offset", 0), block_size=info.get("block_size", 96)
            )
            self.input_connections[io] = rec
            if hasattr(self, "_start_receiver"):
                self._start_receiver(io, rec.mcast_group, rec.block_offset, rec.block_size)
            self._queue_led_update(io, LedState.BLINK_RAPID)

        self.refresh_all_gui()

    def refresh_all_gui(self):
        for jack in self.input_jacks.values():
            jack._set_led()
        for jack in self.output_jacks.values():
            jack._set_led()
        if self.root:
            self.root.update_idletasks()

    def _listen(self):
        while True:
            try:
                data, _ = self.sock.recvfrom(4096)
                msg = ProtocolMessage.unpack(data)
                threading.Thread(target=self.handle_incoming_msg, args=(msg,), daemon=True).start()
            except socket.timeout:
                continue

    def handle_incoming_msg(self, msg: ProtocolMessage):
        for jack in list(self.input_jacks.values()) + list(self.output_jacks.values()):
            if msg.type == ProtocolMessageType.INITIATE.value:
                jack.on_initiate(msg)
            elif msg.type == ProtocolMessageType.CANCEL.value:
                jack.on_cancel(msg)
            elif msg.type == ProtocolMessageType.COMPATIBLE.value and hasattr(jack, "on_compatible"):
                jack.on_compatible(msg)
            elif msg.type == ProtocolMessageType.SHOW_CONNECTED.value and hasattr(jack, "on_show_connected"):
                jack.on_show_connected(msg)

        if msg.type == ProtocolMessageType.PATCH_RESTORE.value:
            payload = msg.payload
            target = payload.get("target_mod")
            if not target or target == self.module_id:
                data = payload.get("payload", payload) if isinstance(payload, dict) else payload
                if isinstance(data, dict):
                    self.iterate_for_restore(data)

        if msg.module_id == "mcu":
            if msg.type == ProtocolMessageType.CAPABILITIES_INQUIRY.value:
                resp = ProtocolMessage(ProtocolMessageType.CAPABILITIES_RESPONSE.value, self.module_id,
                                     payload={"name": self.module_id, "type": self.type})
                self.sock.sendto(resp.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
            elif msg.type == ProtocolMessageType.STATE_INQUIRY.value:
                state = self.iterate_for_save()
                resp = ProtocolMessage(ProtocolMessageType.STATE_RESPONSE.value, self.module_id, payload=state)
                self.sock.sendto(resp.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))

    def on_closing(self):
        try: self.sock.close()
        except: pass
        if self.root: self.root.destroy()