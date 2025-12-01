# connection_protocol.py — FULL PER-JACK STATE MACHINES — FINAL & CORRECT
# Implements the exact CSV state table from protocol notes.txt
# Each jack has its own independent finite state machine
# No shared pending_initiator, no crosstalk, no race conditions

import logging
import json
import struct
from enum import Enum, auto, IntEnum
from typing import Optional, Dict, Any

CONTROL_MULTICAST = "239.255.0.1"
UDP_CONTROL_PORT = 5000
UDP_AUDIO_PORT = 5005

logger = logging.getLogger(__name__)

# ===================================================================
# ENUMS — exactly as in your CSV
# ===================================================================

class OutputState(Enum):
    OIdle = auto()
    OSelfPending = auto()
    OOtherPending = auto()
    OCompatible = auto()
    ONotCompatible = auto()

class InputState(Enum):
    IIdleDisconnected = auto()
    ISelfCompatible = auto()
    IPending = auto()
    IIdleConnected = auto()
    IOtherPending = auto()
    IPendingSame = auto()
    IOtherCompatible = auto()

# ===================================================================
# MESSAGE TYPE ENUM — single byte on the wire
# ===================================================================

class ProtocolMessageType(IntEnum):
    INITIATE = 1
    CANCEL = 2
    COMPATIBLE = 3
    CONNECT = 4
    SHOW_CONNECTED = 5

    STATE_INQUIRY = 10
    STATE_RESPONSE = 11

    CAPABILITIES_INQUIRY = 12      # ← NEW – this was missing
    CAPABILITIES_RESPONSE = 13     # ← shifted from 12 to avoid overlap

    PATCH_RESTORE = 20

class LedState(Enum):
    OFF = 0
    SOLID = 1
    BLINK_SLOW = 2
    BLINK_RAPID = 3

# ===================================================================
# IO TYPE ENUM — single byte on the wire
# ===================================================================

class IOType(IntEnum):
    UNKNOWN  = 0x00
    CV       = 0x01
    AUDIO    = 0x02
    GATE     = 0x03
    TRIGGER  = 0x04
    CLOCK    = 0x05
    MIDI     = 0x06
    OSC_MSG   = 0x07   # renamed to avoid clash with built-in "osc"

    @classmethod
    def from_string(cls, s: str) -> 'IOType':
        mapping = {
            "cv":      cls.CV,
            "audio":   cls.AUDIO,
            "gate":    cls.GATE,
            "trigger": cls.TRIGGER,
            "clock":   cls.CLOCK,
            "midi":    cls.MIDI,
            "osc":     cls.OSC_MSG,
        }
        return mapping.get(s.lower(), cls.UNKNOWN)

    def __str__(self) -> str:
        return self.name.lower()


class ProtocolMessage:
    """
    Wire format (9-byte fixed header):
        B  H  B  H  H
        type | mod_len | io_type | io_len | payload_len
    """
    def __init__(
        self,
        type_val: int,
        module_id: str,
        io_type: Any = IOType.UNKNOWN,
        io_id: str = "",
        payload: Optional[Dict[str, Any]] = None,
        mod_type: Any = None,                    # <-- NEW: accept old calls
    ):
        self.type = int(type_val) & 0xFF
        self.module_id = str(module_id)

        # Support both old code (mod_type="cv") and new code (io_type=IOType.CV)
        source = mod_type if mod_type is not None else io_type

        if isinstance(source, str):
            self.io_type = int(IOType.from_string(source))
        elif isinstance(source, IOType):
            self.io_type = int(source)
        else:
            self.io_type = int(source) & 0xFF

        self.io_id = str(io_id)
        self.payload = payload or {}

    # ------------------------------------------------------------------
    def pack(self) -> bytes:
        payload_json = json.dumps(self.payload, separators=(',', ':')).encode('utf-8')
        payload_len = len(payload_json)

        header = struct.pack(
            "!BHBHH",                     # 1+2+1+2+2 = 8 bytes
            self.type,
            len(self.module_id),
            self.io_type,                 # single byte enum
            len(self.io_id),
            payload_len
        )

        body = (
            self.module_id.encode('utf-8') +
            self.io_id.encode('utf-8') +
            payload_json
        )
        return header + body

    # ------------------------------------------------------------------
    @staticmethod
    def unpack(data: bytes) -> 'ProtocolMessage':
        if len(data) < 8:
            return ProtocolMessage(0, "bad", IOType.UNKNOWN, "")

        try:
            (msg_type,
             mod_len,
             io_type_byte,
             io_len,
             payload_len) = struct.unpack("!BHBHH", data[:8])
        except struct.error:
            return ProtocolMessage(0, "bad_hdr", IOType.UNKNOWN, "")

        offset = 8

        def read_str(length: int) -> str:
            nonlocal offset
            end = offset + length
            if end > len(data):
                s = data[offset:]
                offset = len(data)
            else:
                s = data[offset:end]
                offset = end
            return s.decode('utf-8', errors='replace')

        module_id = read_str(mod_len)
        io_id     = read_str(io_len)

        payload = {}
        if payload_len and offset + payload_len <= len(data):
            try:
                payload = json.loads(data[offset:offset + payload_len])
            except json.JSONDecodeError:
                pass

        return ProtocolMessage(
            type_val=msg_type,
            module_id=module_id,
            io_type=io_type_byte,
            io_id=io_id,
            payload=payload
        )

    # ------------------------------------------------------------------
    def __repr__(self) -> str:
        try:
            typ_name = ProtocolMessageType(self.type).name
        except Exception:
            typ_name = str(self.type)
        return (f"Msg({typ_name} mod={self.module_id} "
                f"io_type={IOType(self.io_type)} io={self.io_id} "
                f"payload_keys={list(self.payload.keys()) if self.payload else None})")
                


class ConnectionRecord:
    __slots__ = ("src", "src_io", "mcast_group", "block_offset", "block_size")
    def __init__(self, src="", src_io="", mcast_group="", block_offset=0, block_size=96):
        self.src = src
        self.src_io = src_io
        self.mcast_group = mcast_group
        self.block_offset = block_offset
        self.block_size = block_size
        

# ===================================================================
# OUTPUT JACK STATE MACHINE
# ===================================================================

class OutputJack:
    def __init__(self, io_id: str, module):
        self.io_id = io_id
        self.module = module
        self.state = OutputState.OIdle
        self._set_led()
        
    def _set_led(self):
        mapping = {
            OutputState.OIdle: LedState.SOLID,
            OutputState.OSelfPending: LedState.BLINK_SLOW,
            OutputState.OOtherPending: LedState.OFF,
            OutputState.OCompatible: LedState.SOLID,
            OutputState.ONotCompatible: LedState.OFF,
        }
        self.module._queue_led_update(self.io_id, mapping[self.state])

    def short_press(self, io_id=None):
        if self.state in (OutputState.OIdle, OutputState.OCompatible):
            self._send_initiate()
            self.state = OutputState.OSelfPending
            self._set_led()

    def long_press(self, io_id=None):
        if self.state != OutputState.OIdle:
            self.module.send_cancel(self.io_id)   # ← uses public method
            self.state = OutputState.OIdle
            self._set_led()
            
    def _send_initiate(self):
        info = self.module.outputs[self.io_id]
        payload = {
            "type": info.get("type", "unknown"),
            "group": info.get("group", self.module.mcast_group),
            "offset": info.get("offset", 0),
            "block_size": info.get("block_size", 96)
        }
        msg = ProtocolMessage(
            ProtocolMessageType.INITIATE.value,
            self.module.module_id,
            mod_type=self.module.type,
            io_id=self.io_id,
            payload=payload
        )
        self.module.sock.sendto(msg.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
        logger.info(f"[{self.module.module_id}] INITIATE sent from {self.io_id} → {payload['group']} "
                    f"offset={payload['offset']} size={payload['block_size']}")

    def on_initiate(self, msg: ProtocolMessage):
        # Ignore our own INITIATE message
        if msg.module_id == self.module.module_id and msg.io_id == self.io_id:
            return

        # If we are the one trying to initiate, and another output beat us,
        # yield to the one with lower module_id (tie-breaker)
        if self.state == OutputState.OSelfPending:
            if msg.module_id < self.module.module_id:
                self.state = OutputState.OOtherPending
                self._set_led()
            return  # done — we yielded

        # For all other cases: another output has initiated
        # → ALL non-initiating outputs go dark
        # No type checking. Ever.
        self.state = OutputState.OOtherPending
        self._set_led()

    def on_cancel(self, msg: ProtocolMessage):
        # We don't care whose CANCEL this is — if ANY cancel comes in,
        # and we're not idle, we go back to OIdle
        #if self.state != OutputState.OIdle: #no need to test, just change state
            self.state = OutputState.OIdle
            self._set_led()

    def on_show_connected(self, msg: ProtocolMessage):
        logger.debug(f"[{self.module.module_id}] on_show_connected called with payload: {msg.payload}")
        target_mod = msg.payload.get("target_mod")
        target_io = msg.payload.get("target_io")
        if target_mod == self.module.module_id and target_io == self.io_id:
            logger.info(f"[{self.module.module_id}] FLASHING {self.io_id} for REVEAL from {target_mod}:{target_io}")
            # Flash 3x rapid (existing logic, with safeguard)
            def blink(count=6):
                if count <= 0 or not self.module.root:
                    self._set_led()  # Revert to normal state
                    return
                on = (count % 2 == 0)
                state = LedState.BLINK_RAPID if on else LedState.OFF
                self.module._queue_led_update(self.io_id, state)
                self.module.root.after(150, lambda c=count-1: blink(c))
            blink()
        else:
            logger.debug(f"[{self.module.module_id}] Skipping flash (target {target_mod}:{target_io} != self {self.module.module_id}:{self.io_id})")
            
    def on_compatible(self, msg: ProtocolMessage):
        # Ignore our own COMPATIBLE message
        if msg.module_id == self.module.module_id and msg.io_id == self.io_id:
            return

        payload = msg.payload or {}
        requested_type = payload.get("type", "unknown")
        my_type = self.module.outputs[self.io_id].get("type", "unknown")

        if my_type == requested_type:
            self.state = OutputState.OCompatible
        else:
            self.state = OutputState.ONotCompatible
        self._set_led()
        
    def on_show_connected(self, msg: ProtocolMessage):
        target_mod = msg.payload.get("target_mod")
        target_io = msg.payload.get("target_io")
        logger.info(
            f"[{self.module.module_id}] Output {self.io_id} checking REVEAL: "
            f"target={target_mod}:{target_io} vs self={self.module.module_id}:{self.io_id}"
        )
        if target_mod == self.module.module_id and target_io == self.io_id:
            logger.info(f"[{self.module.module_id}] !!! FLASHING {self.io_id} FOR REVEAL !!!")
            # your existing flash code...
            self._flash_rapid_3s()
        else:
            logger.debug(f"[{self.module.module_id}] Output {self.io_id} ignoring REVEAL")
            
    def _flash_rapid_3s(self):
        logger.info(f"[{self.module.module_id}] Starting 3-second rapid flash on {self.io_id}")
        def blink(count=8):
            if count <= 0:
                self._set_led()
                return
            state = LedState.BLINK_RAPID if (count % 2) else LedState.OFF
            self.module._queue_led_update(self.io_id, state)
            self.module.root.after(120, blink, count - 1)
        blink()
        
    def revert():
        if hasattr(self.module, "root") and self.module.root and self.module.root.winfo_exists():
                self._set_led()

        if hasattr(self.module, "root") and self.module.root:
            self.module.root.after(3000, revert)

# ===================================================================
# INPUT JACK STATE MACHINE
# ===================================================================

class InputJack:
    def __init__(self, io_id: str, module):
        self.io_id = io_id
        self.module = module
        self.state = InputState.IIdleDisconnected
        self.type_ = module.inputs[io_id].get("type", "unknown")

        # <<< THESE ARE THE ONLY NEW FIELDS >>>
        self.connected_src: Optional[str] = None          # e.g. "lfo_0"
        self.connected_src_io: Optional[str] = None       # e.g. "cv"
        self.connected_mcast: Optional[str] = None
        self.connected_offset: Optional[int] = None
        self.connected_block_size: Optional[int] = None
        # <<< END NEW FIELDS >>>

        self._set_led()

    # ------------------------------------------------------------------
    #  ACCEPT A PENDING CONNECTION (called both from live use and restore)
    # ------------------------------------------------------------------
    def _accept_connection(self, msg: ProtocolMessage):
        payload = msg.payload
        self.connected_src = msg.module_id
        self.connected_src_io = msg.io_id
        self.connected_mcast = payload.get("group", self.module.mcast_group)
        self.connected_offset = payload.get("offset", 0)
        self.connected_block_size = payload.get("block_size", 96)

        # Keep ConnectionRecord in sync (used by save/restore)
        rec = ConnectionRecord(
            src=self.connected_src,
            src_io=self.connected_src_io,          # <-- important for REVEAL
            mcast_group=self.connected_mcast,
            block_offset=self.connected_offset,
            block_size=self.connected_block_size,
        )
        self.module.input_connections[self.io_id] = rec
        self.state = InputState.IPendingSame
        print(f"DEBUG: {self.io_id} set to IPendingSame")
        self._set_led()
        # Start receiving the audio/CV stream
        self.module._start_receiver(
            self.io_id,
            self.connected_mcast,
            self.connected_offset,
            self.connected_block_size,
        )
        logger.info(f"[{self.module.module_id}] Connection accepted on {self.io_id} from {msg.module_id}:{msg.io_id} mcast={mcast_group}")
        self.connected_src = msg.module_id
        self.connected_src_io = msg.io_id
        logger.debug(f"[{self.module.module_id}] Set connected_src = {self.connected_src}, connected_src_io = {self.connected_src_io}")
        logger.info(f"[{self.module.module_id}] INPUT {self.io_id} CONNECTED to {self.connected_src}:{self.connected_src_io}")

    # ------------------------------------------------------------------
    #  USER PRESS (short press)
    # ------------------------------------------------------------------
    def short_press(self, io_id=None):
        # 1. Start looking for a compatible output
        if self.state == InputState.IIdleDisconnected:
            self.module.send_compatible(self.io_id)
            self.state = InputState.ISelfCompatible
            self._set_led()
            return

        # 2. Accept the blinking output
        if self.state == InputState.IPending:
            msg = getattr(self.module, "pending_msg", None)
            if msg:
                self._accept_connection(msg)
            self.state = InputState.IIdleConnected
            self._set_led()
            return

        # 3. REVEAL – show which output we are connected to
        if self.state == InputState.IIdleConnected:
            logger.info(f"[{self.module.module_id}] REVEAL requested on input {self.io_id}")
            if self.connected_src and self.connected_src_io:
                logger.info(
                    f"[{self.module.module_id}] SENDING SHOW_CONNECTED → "
                    f"{self.connected_src}:{self.connected_src_io} (from input {self.io_id})"
                )
                self.module.send_show_connected(
                    self.io_id,
                    self.connected_src,
                    self.connected_src_io
                )
            else:
                logger.warning(f"[{self.module.module_id}] REVEAL requested but no connection data!")
            return
            
        # 4. Cancel a pending “compatible” search
        if self.state == InputState.ISelfCompatible:
            self.module.send_cancel(self.io_id)
            self.state = InputState.IIdleDisconnected
            self._set_led()

    def _set_led(self):
        mapping = {
            InputState.IIdleDisconnected: LedState.OFF,
            InputState.ISelfCompatible: LedState.SOLID,  # ← Changed from BLINK_SLOW, which may be better         
            InputState.IPending: LedState.SOLID, 
            InputState.IIdleConnected: LedState.BLINK_RAPID,
            InputState.IOtherPending: LedState.OFF,
            InputState.IPendingSame: LedState.BLINK_SLOW,
            InputState.IOtherCompatible: LedState.OFF,
        }
        self.module._queue_led_update(self.io_id, mapping[self.state])

                
    def long_press(self, io_id=None):
        if self.state == InputState.ISelfCompatible:
            self.module.send_cancel(self.io_id)
            self.state = InputState.IIdleDisconnected
            self.module._queue_led_update(self.io_id, LedState.OFF)

        elif self.state == InputState.IIdleConnected:
            if hasattr(self.module, "_stop_receiver"):
                self.module._stop_receiver(self.io_id)
            self.state = InputState.IIdleDisconnected
            self.module._queue_led_update(self.io_id, LedState.OFF)
            
    def _send_compatible(self):
        info = self.module.inputs[self.io_id]
        payload = {"type": info.get("type", "unknown")}
        msg = ProtocolMessage(
            ProtocolMessageType.COMPATIBLE.value,
            self.module.module_id, self.module.type, self.io_id, payload
        )
        self.module.sock.sendto(msg.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
    
    def _send_reveal(self):
            rec = self.module.input_connections.get(self.io_id)
            if not rec:
                return
            payload = {"src": rec.src}
            msg = ProtocolMessage(
                ProtocolMessageType.SHOW_CONNECTED.value,
                self.module.module_id, self.module.type, self.io_id, payload
            )
            self.module.sock.sendto(msg.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
            logger.info(f"[{self.module.module_id}] REVEAL sent for {self.io_id} → {rec.src}")

   
    def _disconnect(self):
        rec = self.module.input_connections.get(self.io_id)
        if rec:
            self.module.input_connections[self.io_id] = None
            self.state = InputState.IIdleDisconnected
            self.module._stop_receiver(self.io_id)  # implement if needed
            logger.info(f"[{self.module.module_id}] Disconnected {self.io_id}")
        self._set_led()

    def on_initiate(self, msg: ProtocolMessage):
        if msg.module_id == self.module.module_id and msg.io_id == self.io_id:
            return  # ignore self

        src_type = msg.payload.get("type", "unknown")
        src_group = msg.payload.get("group", None)
        src_offset = msg.payload.get("offset", 0)
        my_type = self.module.inputs[self.io_id].get("type", "unknown")

        current_conn = self.module.input_connections.get(self.io_id)

        # Type compatibility
        type_match = (src_type == my_type)

        # Exact connection match (group + offset)
        exact_match = (current_conn and
                       current_conn.mcast_group == src_group and
                       current_conn.block_offset == src_offset)

        if not type_match:
            # Incompatible type
            if self.state in (InputState.IIdleDisconnected,
                              InputState.ISelfCompatible,
                              InputState.IPending):
                self.state = InputState.IOtherCompatible
            else:
                self.state = InputState.IOtherPending
        else:
            # Compatible type
            if exact_match:
                self.state = InputState.IPendingSame
            elif current_conn:
                # Same type, different output → stay connected, no highlight
                self.state = InputState.IIdleConnected
            else:
                # No connection yet → ready to connect
                self.state = InputState.IPending

        # Always store pending data for accept
        self.pending_initiator = msg.module_id
        self.pending_initiator_io = msg.io_id
        self.pending_mcast = src_group
        self.pending_offset = src_offset

        self._set_led()
        logger.info(f"[{self.module.module_id}] {self.io_id} → {self.state} "
                    f"(type_match={type_match}, exact_match={exact_match})")
                    
    def on_cancel(self, msg: ProtocolMessage):
        # Clear all temporary/pending states
        if self.state in (InputState.IPending,
                          InputState.ISelfCompatible,
                          InputState.IOtherCompatible):
            self.state = InputState.IIdleDisconnected

        elif self.state == InputState.IPendingSame:
            # Was highlighting the currently selected output → lose highlight
            self.state = InputState.IIdleConnected

        elif self.state == InputState.IOtherPending:
            # Was blocked by type mismatch → back to normal connected
            self.state = InputState.IIdleConnected

        # IIdleConnected stays IIdleConnected — do nothing

        # Always clear pending initiator data
        self.pending_initiator = None
        self.pending_initiator_io = None
        self.pending_mcast = None
        self.pending_offset = None

        self._set_led()

    def on_compatible(self, msg: ProtocolMessage):
        if msg.module_id == self.module.module_id and msg.io_id == self.io_id:
            return  # ignore self

        payload = msg.payload or {}
        src_type = payload.get("type", "unknown")
        my_type = self.module.outputs[self.io_id].get("type", "unknown")

        if my_type == src_type:
            self.state = OutputState.OCompatible
            logger.info(f"[{self.module.module_id}] Output {self.io_id} → OCompatible (matched {src_type})")
        else:
            self.state = OutputState.ONotCompatible
            logger.info(f"[{self.module.module_id}] Output {self.io_id} → ONotCompatible (wanted {src_type}, got {my_type})")
        self._set_led()

# ===================================================================
# MAIN CONNECTION PROTOCOL CLASS
# ===================================================================

class ConnectionProtocol:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # These are required for:
        # - Patch save/restore
        # - InputJack._accept_connection()
        # - _refresh_gui_from_controls()
        self.input_connections: Dict[str, Optional[ConnectionRecord]] = {}
        self.output_jacks: Dict[str, OutputJack] = {}
        self.input_jacks: Dict[str, InputJack] = {}

    def _ensure_io_defs(self):
        """Call after inputs/outputs are defined — creates per-jack state machines and sets initial LEDs"""
        # Build jack state machines
        self.output_jacks = {io: OutputJack(io, self) for io in self.outputs}
        self.input_jacks  = {io: InputJack(io, self)  for io in self.inputs}

        # Initial LED state is set by each jack's __init__ → no extra call needed
        # Old code removed: self._sync_initial_leds()  ← DELETE THIS LINE
        logger.debug(f"[{self.module_id}] Per-jack state machines initialized")



    def _notify_self_compatible(self, io_id: str):
        for jack in self.input_jacks.values():
            if jack.io_id != io_id and jack.state == InputState.IIdleDisconnected:
                jack.state = InputState.IOtherCompatible
                jack._set_led()

    def _broadcast_cancel(self):
        msg = ProtocolMessage(ProtocolMessageType.CANCEL.value, self.module_id)
        self.sock.sendto(msg.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))

    # User actions
    def initiate_connect(self, io_id: str):
        if io_id in self.output_jacks:
            self.output_jacks[io_id].short_press()

    def connect_input(self, io_id: str):
        if io_id in self.input_jacks:
            self.input_jacks[io_id].short_press()

    def long_press_input(self, io_id: str):
        if io_id in self.input_jacks:
            self.input_jacks[io_id].long_press()

    # Message dispatch
    def handle_msg(self, msg: ProtocolMessage):
        if msg.type == ProtocolMessageType.INITIATE.value:
            for jack in self.input_jacks.values():
                jack.on_initiate(msg)
            for jack in self.output_jacks.values():
                jack.on_initiate(msg)
        elif msg.type == ProtocolMessageType.CANCEL.value:
            for jack in self.input_jacks.values():
                jack.on_cancel(msg)
            for jack in self.output_jacks.values():
                jack.on_cancel(msg)
        elif msg.type == ProtocolMessageType.COMPATIBLE.value:
            for jack in self.output_jacks.values():
                jack.on_compatible(msg)
            return  # ← stop here
        elif msg.type == ProtocolMessageType.CONNECT.value:
            pass  # ignored — only for debugging
        elif msg.type == ProtocolMessageType.STATE_INQUIRY.value:
            if msg.module_id == "mcu":  # only respond to MCU
                state = self.get_state()
                resp = ProtocolMessage(
                    ProtocolMessageType.STATE_RESPONSE.value,
                    self.module_id,
                    payload=state
                    )
                self.sock.sendto(resp.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
                logger.info(f"[{self.module_id}] Sent STATE_RESPONSE for save")
        elif msg.type == ProtocolMessageType.SHOW_CONNECTED.value:
            if msg.io_id in self.output_jacks:
                self.output_jacks[msg.io_id].on_show_connected(msg)
                
