# base_module.py
import socket
import threading
import struct
import time
import json
from enum import Enum
from typing import Dict, Any
import logging
import queue

logger = logging.getLogger(__name__)

CONTROL_MULTICAST = '239.50.0.1'
UDP_CONTROL_PORT = 5004
RECV_TIMEOUT = 0.001
BLINK_SLOW_MS = 500
BLINK_RAPID_MS = 100

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

class ProtocolMessage:
    def __init__(self, type: int, module_id: str, mod_type: str = '', io_id: str = '', payload: Dict or bytes = None):
        self.type = type
        self.module_id = module_id
        self.mod_type = mod_type
        self.io_id = io_id
        self.payload = payload or {}

    def pack(self) -> bytes:
        type_byte = struct.pack('B', self.type)
        pad_str = lambda s: s.ljust(32, '\0')[:32].encode()
        payload_bytes = json.dumps(self.payload).encode() if isinstance(self.payload, dict) else self.payload
        return type_byte + pad_str(self.module_id) + pad_str(self.mod_type) + pad_str(self.io_id) + payload_bytes

    @classmethod
    def unpack(cls, data: bytes):
        type_val = struct.unpack('B', data[0:1])[0]
        module_id = data[1:33].decode('utf-8').rstrip('\0')
        mod_type = data[33:65].decode('utf-8').rstrip('\0')
        io_id = data[65:97].decode('utf-8').rstrip('\0')
        payload_data = data[97:]
        try:
            payload = json.loads(payload_data)
        except json.JSONDecodeError:
            payload = payload_data
        return cls(type_val, module_id, mod_type, io_id, payload)

class BaseModule:
    def __init__(self, mod_id: str, mod_type: str):
        self.module_id = mod_id
        self.type = mod_type
        self.inputs = {}  # io_id: {'type': str, 'group': str, 'src': str}
        self.outputs = {}  # io_id: {'type': str, 'group': str}
        self.controls = {}  # id: value
        self.control_ranges = {}  # id: [min, max]
        self.led_states = {}  # io_id: LedState
        self.gui_leds = {}  # io_id: tk.Label
        self.pending_io = None
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        self.sock.bind(('', UDP_CONTROL_PORT))
        mreq = struct.pack("4sl", socket.inet_aton(CONTROL_MULTICAST), socket.INADDR_ANY)
        self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        self.sock.settimeout(RECV_TIMEOUT)
        self.lock = threading.Lock()
        self.gui_queue = queue.Queue(maxsize=32)
        self._listener_thread = threading.Thread(target=self._listen, daemon=True)
        self._listener_thread.start()
        self._blink_thread = threading.Thread(target=self._blink_loop, daemon=True)
        self._blink_thread.start()

    def _listen(self):
        while True:
            try:
                data, _ = self.sock.recvfrom(1024)
                msg = ProtocolMessage.unpack(data)
                threading.Thread(target=self.handle_msg, args=(msg,), daemon=True).start()
            except socket.timeout:
                pass
            except Exception as e:
                logger.warning(f"Listen error in {self.module_id}: {e}")

    def _blink_loop(self):
        while True:
            time.sleep(0.05)
            with self.lock:
                for io, state in list(self.led_states.items()):
                    if state == LedState.BLINK_SLOW:
                        ms = int(time.time() * 1000) % (BLINK_SLOW_MS * 2)
                        self.led_states[io] = LedState.SOLID if ms < BLINK_SLOW_MS else LedState.OFF
                    elif state == LedState.BLINK_RAPID:
                        ms = int(time.time() * 1000) % (BLINK_RAPID_MS * 2)
                        self.led_states[io] = LedState.SOLID if ms < BLINK_RAPID_MS else LedState.OFF
            with self.lock:
                for io, state in self.led_states.items():
                    try:
                        self.gui_queue.put_nowait((io, state.name))
                    except queue.Full:
                        pass

    def _update_display(self):
        while True:
            try:
                io, state_str = self.gui_queue.get_nowait()
                if io in self.gui_leds:
                    color = {'OFF': 'gray', 'SOLID': 'green', 'BLINK_SLOW': 'yellow', 'BLINK_RAPID': 'orange', 'ERROR': 'red'}.get(state_str, 'gray')
                    self.gui_leds[io].config(background=color)
            except queue.Empty:
                break

    def handle_msg(self, msg: ProtocolMessage):
        pass

    def _start_receiver(self, io, group):
        threading.Thread(target=self._receiver_stub, args=(io, group), daemon=True).start()

    def _receiver_stub(self, io, group):
        logger.info(f"Receiver started for {io} on {group}")

    def on_closing(self):
        self.sock.close()