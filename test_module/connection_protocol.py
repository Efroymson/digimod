# connection_protocol.py — FULL PER-JACK STATE MACHINES — FINAL & CORRECT
# Implements the exact CSV state table from protocol notes.txt
# Each jack has its own independent finite state machine
# No shared pending_initiator, no crosstalk, no race conditions

import logging
from enum import Enum, auto
from typing import Optional, Dict, Any
from base_module import (
    ProtocolMessage, ProtocolMessageType, CONTROL_MULTICAST, UDP_CONTROL_PORT,
    LedState, ConnectionRecord
)

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
            "group": info.get("group", self.module.mcast_group),
            "type": info.get("type", "unknown"),
            "offset": 0,
            "block_size": 96
        }
        msg = ProtocolMessage(
            ProtocolMessageType.INITIATE.value,
            self.module.module_id, self.module.type, self.io_id, payload
        )
        self.module.sock.sendto(msg.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
        logger.info(f"[{self.module.module_id}] INITIATE sent from {self.io_id}")

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
        if msg.io_id == self.io_id and msg.module_id == self.module.module_id:
            # Flash 3× rapid
            for i in range(6):
                on = (i % 2 == 0)
                self.module.root.after(100 * i,
                    lambda on=on: self.module._queue_led_update(self.io_id,
                        LedState.BLINK_RAPID if on else LedState.OFF))
            self.module.root.after(600, lambda: self._set_led())
    
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
        """REVEAL: Flash rapidly for 3s if this output is the connected source"""
        payload = msg.payload or {}
        if payload.get("src") == self.module.module_id and payload.get("src_io") == self.io_id:
            logger.info(f"[{self.module.module_id}] REVEAL → flashing {self.io_id} for 3s")
            self._flash_rapid_3s()

    def _flash_rapid_3s(self):
        """Temporarily override LED to rapid blink for 3 seconds"""
        original_state = self.state
        self.module._queue_led_update(self.io_id, LedState.BLINK_RAPID)

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

        # Start receiving the audio/CV stream
        self.module._start_receiver(
            self.io_id,
            self.connected_mcast,
            self.connected_offset,
            self.connected_block_size,
        )
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
            if self.connected_src and self.connected_src_io:
                payload = {
                    "target_mod": self.connected_src,
                    "target_io":  self.connected_src_io
                }
                msg = ProtocolMessage(
                    ProtocolMessageType.SHOW_CONNECTED.value,
                    self.module.module_id,
                    io_id=self.io_id,
                    payload=payload
                )
                self.module.sock.sendto(msg.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
                logger.debug(f"[{self.module.module_id}] REVEAL → {self.connected_src}:{self.connected_src_io}")
            return

        # 4. Cancel a pending “compatible” search
        if self.state == InputState.ISelfCompatible:
            self.module.send_cancel(self.io_id)
            self.state = InputState.IIdleDisconnected
            self._set_led()

    def _set_led(self):
        mapping = {
            InputState.IIdleDisconnected: LedState.OFF,
            InputState.ISelfCompatible: LedState.BLINK_SLOW,
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
        # Ignore our own INITIATE messages
        if msg.module_id == self.module.module_id and msg.io_id == self.io_id:
            return

        # Only react when we're waiting for a connection
        if self.state not in (InputState.ISelfCompatible, InputState.IIdleDisconnected):
            return

        # Extract offered type from INITIATE payload
        src_type = msg.payload.get("type", "unknown")
        my_type = self.module.inputs[self.io_id].get("type", "unknown")

        logger.info(f"INITIATE from {msg.module_id}:{msg.io_id} type='{src_type}' → my type='{my_type}'")

        if src_type == my_type:
            # Compatible — go pending
            self.state = InputState.IPending
            self.pending_initiator = (msg.module_id, msg.io_id)
            logger.debug(f"[{self.module.module_id}] {self.io_id} PENDING ← {msg.module_id}:{msg.io_id}")
        else:
            # Not compatible — reject
            self.state = InputState.IOtherPending
            logger.debug(f"[{self.module.module_id}] {self.io_id} REJECTED (type mismatch)")

        self._set_led()

    def on_cancel(self, msg: ProtocolMessage):
        if self.state in (InputState.IPending, InputState.IPendingSame,
                          InputState.ISelfCompatible, InputState.IOtherCompatible,
                          InputState.IOtherPending):
            self.state = InputState.IIdleDisconnected
            self.pending_initiator = None
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
                
