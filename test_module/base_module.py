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
    next_ip_octet = 1

    def __init__(self, mod_id: str, mod_type: str):
        self.module_id = mod_id
        self.type = mod_type
        octet = BaseModule.next_ip_octet
        self.ip = f'127.0.1.{octet}'
        BaseModule.next_ip_octet = min(255, octet + 1)
        self.inputs = {}
        self.outputs = {}
        self.controls = {}
        self.control_ranges = {}
        self.led_states = {}
        self.gui_leds = {}
        self.last_push_time = {}  # Initialize here to avoid AttributeError
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        self.sock.bind(('', UDP_CONTROL_PORT))
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
            # Start periodic drain for reliable GUI updates
            self.root.after(50, self._periodic_drain)

    def _periodic_drain(self):
        """Periodic GUI queue drain for reliable LED updates."""
        if self.root and self.root.winfo_exists():
            self._update_display()
            self.root.after(50, self._periodic_drain)

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

    def _update_display(self):
        while True:
            try:
                io, state_str = self.gui_queue.get_nowait()
                if io in self.gui_leds:
                    color_map = {
                        'OFF': 'gray',
                        'BLINK_SLOW': 'yellow',
                        'BLINK_RAPID': 'red',
                        'SOLID': 'green',
                        'ERROR': 'orange'
                    }
                    color = color_map.get(state_str, 'gray')
                    self.gui_leds[io].config(background=color, text=f"{io.upper()} {state_str}")
                    logger.debug(f"LED {io}: {state_str} → bg={color}")
            except queue.Empty:
                break

    def _drain_queue_once(self):
        if not self.root or not self.root.winfo_exists():
            return
        self._update_display()
        if not self.gui_queue.empty() and self.root:
            self.root.after(100, self._drain_queue_once)

    def _sync_ui(self):
        self._update_display()

    def _queue_led_update(self, io: str, state: LedState):
        # Ensure last_push_time exists
        if not hasattr(self, 'last_push_time'):
            self.last_push_time = {}
        now = time.time()
        if io in self.last_push_time and now - self.last_push_time[io] < 0.1:
            return
        self.last_push_time[io] = now
        self.led_states[io] = state
        try:
            self.gui_queue.put_nowait((io, state.name))
        except queue.Full:
            logger.warning(f"LED queue full for {io}: Drop")
        # With periodic drain, no need for immediate schedule

    def _sync_initial_leds(self):
        """Set initial LED states: outputs SOLID, connected inputs BLINK_RAPID, unconnected OFF."""
        for io in self.outputs:
            self._queue_led_update(io, LedState.SOLID)
        for io in self.inputs:
            state = LedState.BLINK_RAPID if self.inputs[io].get("src") else LedState.OFF
            self._queue_led_update(io, state)

    def get_capabilities(self) -> Dict:
        return {
            "ip": self.ip,  # Static: Add here for discovery/routing.  In "real harware" also collect MAC address
            "name": self.module_id,
            "type": self.type,
            "controls": [{"id": k, "range": v, "default": self.controls.get(k, v[0] if v else 0)} for k, v in self.control_ranges.items()],
            "inputs": [{"id": k, "type": v['type']} for k, v in self.inputs.items()],
            "outputs": [{"id": k, "type": v['type']} for k, v in self.outputs.items()]
        }

    def get_state(self) -> Dict:
        return {
            "controls": self.controls.copy(),  # Dynamic only
            "inputs": {k: {"src": v.get("src"), "group": v.get("group")} for k, v in self.inputs.items()},
            "outputs": self.outputs.copy()
        }
        
    def restore_patch(self, data: bytes or Dict):
        if isinstance(data, bytes):
            data = json.loads(data)
        for k, v in data.get("controls", {}).items():
            if k in self.control_ranges:
                r = self.control_ranges[k]
                self.controls[k] = max(r[0], min(r[1], v))
        for io, conn in data.get("inputs", {}).items():
            if io in self.inputs:
                self.inputs[io]["src"] = conn.get("src")
                self.inputs[io]["group"] = conn.get("group")
                if self.inputs[io]["group"]:
                    self._start_receiver(io, self.inputs[io]["group"])
        self._sync_initial_leds()
        logger.info(f"{self.module_id}: Patch restored")

    def handle_msg(self, msg: ProtocolMessage):
        pass

    def _start_receiver(self, io, group):
        threading.Thread(target=self._receiver_stub, args=(io, group), daemon=True).start()

    def _receiver_stub(self, io, group):
        logger.info(f"Receiver started for {io} on {group}")

    def on_closing(self):
        self.sock.close()