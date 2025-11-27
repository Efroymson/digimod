import socket
import struct
import threading
import time
import json
from enum import Enum
from typing import Dict, Any
import logging
import queue
from tkinter import ttk
import tkinter as tk

logger = logging.getLogger(__name__)

CONTROL_MULTICAST = '239.50.0.1'
UDP_CONTROL_PORT = 5004
RECV_TIMEOUT = 0.1

class ConnectionRecord:
    def __init__(self, src: str, src_io: str, mcast_group: str, block_offset: int, block_size: int):
        self.src = src                  # e.g. "lfo_0"
        self.src_io = src_io            # e.g. "cv" or "audio"
        self.mcast_group = mcast_group
        self.block_offset = block_offset
        self.block_size = block_size
        
class LedState(Enum):
    OFF = 0
    BLINK_SLOW = 1
    BLINK_RAPID = 2
    SOLID = 3
    ERROR = 4

class ProtocolMessageType(Enum):
    CAPABILITIES_INQUIRY = 1
    CAPABILITIES_RESPONSE = 2
    STATE_INQUIRY = 3
    STATE_RESPONSE = 4
    PATCH_RESTORE = 5
    INITIATE = 6
    CONNECT = 7
    CANCEL = 8
    COMPATIBLE = 9
    SHOW_CONNECTED = 10

class ProtocolMessage:
    def __init__(self, type_val: int, module_id: str, mod_type: str = '', io_id: str = '', payload: Any = None):
        self.type = type_val
        self.module_id = module_id
        self.mod_type = mod_type
        self.io_id = io_id
        self.payload = payload or {}

    def pack(self) -> bytes:
        type_byte = struct.pack('B', self.type)
        pad = lambda s: s.ljust(32, '\0')[:32].encode()
        payload_bytes = json.dumps(self.payload).encode('utf-8') if isinstance(self.payload, dict) else b''
        return type_byte + pad(self.module_id) + pad(self.mod_type) + pad(self.io_id) + payload_bytes[:128]

    @classmethod
    def unpack(cls, data: bytes):
        type_val = struct.unpack('B', data[0:1])[0]
        module_id = data[1:33].decode('utf-8').rstrip('\0')
        mod_type = data[33:65].decode('utf-8').rstrip('\0')
        io_id = data[65:97].decode('utf-8').rstrip('\0')
        payload_data = data[97:97+128]
        try:
            payload = json.loads(payload_data.decode('utf-8').rstrip('\0'))
        except Exception:
            payload = {}
        return cls(type_val, module_id, mod_type, io_id, payload)

class JackWidget(tk.Label):
    def __init__(self, parent, io_id: str, label_text: str,
                 short_press_callback, long_press_callback=None, verbose_text=False):
        super().__init__(parent, text=label_text, bg="gray",
                         width=12, height=2, relief="raised",
                         borderwidth=2, font=("Arial", 10, "bold"))
        self.io_id = io_id
        self.short_press_callback = short_press_callback
        self.long_press_callback = long_press_callback
        self.verbose_text = verbose_text
        self.press_start_time = 0
        self.long_press_id = None
        self.original_bg = "gray"

        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Enter>", lambda e: self.config(cursor="hand2"))
        self.bind("<Leave>", lambda e: self.config(cursor=""))

    def _on_press(self, event):
        self.press_start_time = time.time()
        self.original_bg = self.cget("bg")
        self.config(bg="#d3d3d3")
        if self.long_press_callback:
            self.long_press_id = self.after(300, self._trigger_long_press)

    def _on_release(self, event):
        duration = time.time() - self.press_start_time
        if self.long_press_id:
            self.after_cancel(self.long_press_id)
            self.long_press_id = None
        self.config(bg=self.original_bg)
        if duration < 0.3:
            if self.short_press_callback:
                self.short_press_callback(self.io_id)
            self._flash("green", 100)

    def _trigger_long_press(self):
        self.long_press_id = None
        if self.long_press_callback:
            self.long_press_callback(self.io_id)
        self._flash("lightgray", 50)

    def _flash(self, color: str, ms: int):
        self.config(bg=color)
        self.after(ms, lambda: self.config(bg=self.original_bg))

    def update_led(self, state: LedState):
        colors = {0: 'gray', 1: 'yellow', 2: 'red', 3: 'green', 4: 'orange'}
        color = colors.get(state.value, 'gray')
        base = self.cget("text").split(" [")[0]
        suffix = f" [{state.name}]" if self.verbose_text else ""
        self.config(bg=color, text=base + suffix)
        self.original_bg = color

class BaseModule:
    next_octet = 99

    def __init__(self, mod_id: str, mod_type: str):
        self.module_id = mod_id
        self.type = mod_type
        BaseModule.next_octet += 1
        octet = BaseModule.next_octet
        self.ip = f"127.0.0.{octet}"
        self.mcast_group = f"239.100.0.{octet}"

        self.inputs = {}
        self.outputs = {}
        self.controls = {}
        self.control_ranges = {}

        self.gui_leds = {}
        self.last_push_time = {}

        # Socket — shared port, bound to all interfaces
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, 'SO_REUSEPORT'):
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        self.sock.bind(('', UDP_CONTROL_PORT))  # ← FIXED: Bind to all interfaces

        # Join multicast group
        mreq = struct.pack("4sl", socket.inet_aton(CONTROL_MULTICAST), socket.INADDR_ANY)
        self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

        self.sock.settimeout(RECV_TIMEOUT)

        self.root = None
        self.gui_queue = queue.Queue(maxsize=32)

        self._listener_thread = threading.Thread(target=self._listen, daemon=True)
        self._listener_thread.start()

    def set_root(self, root):
        self.root = root
        if root:
            self.root.after(16, self._periodic_drain)

    def _periodic_drain(self):
        if self.root and self.root.winfo_exists():
            self._update_display()
            self.root.after(16, self._periodic_drain)

    def _listen(self):
        while True:
            try:
                data, _ = self.sock.recvfrom(4096)
                msg = ProtocolMessage.unpack(data)
                threading.Thread(target=self.handle_msg, args=(msg,), daemon=True).start()
            except socket.timeout:
                continue
            except Exception as e:
                logger.debug(f"[{self.module_id}] recv error: {e}")

    def _update_display(self):
        try:
            while True:
                io, state_str = self.gui_queue.get_nowait()
                if io in self.gui_leds:
                    self.gui_leds[io].update_led(LedState[state_str])
        except queue.Empty:
            pass

    def _queue_led_update(self, io: str, state: LedState):
        now = time.time()
        if io in self.last_push_time and now - self.last_push_time[io] < 0.1:
            return
        self.last_push_time[io] = now
        try:
            self.gui_queue.put_nowait((io, state.name))
        except queue.Full:
            pass

    def get_capabilities(self) -> Dict:
        return {
            "name": self.module_id,
            "type": self.type,
            "ip": self.ip,
            "controls": [{"id": k, "range": v, "default": self.controls.get(k, v[0] if v else 0)}
                         for k, v in self.control_ranges.items()],
            "inputs": [{"id": k, "type": v['type'], "group": v.get('group', '')}
                       for k, v in self.inputs.items()],
            "outputs": [{"id": k, "type": v['type'], "group": v.get('group', self.mcast_group)}
                        for k, v in self.outputs.items()]
        }

    def handle_msg(self, msg: ProtocolMessage):
        # Only respond to explicit MCU inquiries
        if msg.type == ProtocolMessageType.CAPABILITIES_INQUIRY.value and msg.module_id == "mcu":
            caps = self.get_capabilities()
            resp = ProtocolMessage(
                ProtocolMessageType.CAPABILITIES_RESPONSE.value,
                self.module_id,
                payload=caps
            )
            self.sock.sendto(resp.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
            logger.info(f"[{self.module_id}] Responded to CAPABILITIES_INQUIRY")

        elif msg.type == ProtocolMessageType.STATE_INQUIRY.value and msg.module_id == "mcu":
            # PatchProtocol handles STATE_RESPONSE
            pass  # Let PatchProtocol's handle_msg see it

        # Let subclasses (ConnectionProtocol, PatchProtocol) see all messages
        # No super() needed — we're the base

    def on_closing(self):
        try:
            self.sock.close()
        except:
            pass