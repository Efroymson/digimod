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

    def short_press(self):
        if self.state == OutputState.OIdle:
            self._send_initiate()
            self.state = OutputState.OSelfPending
        elif self.state == OutputState.OSelfPending:
            self.module._broadcast_cancel()
            self.state = OutputState.OIdle
        elif self.state in (OutputState.OCompatible,):
            self._send_initiate()
            self.state = OutputState.OSelfPending
        # else: do nothing (OOtherPending, ONotCompatible)
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
        if self.state in (OutputState.OSelfPending, OutputState.OOtherPending,
                          OutputState.OCompatible, OutputState.ONotCompatible):
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

# ===================================================================
# INPUT JACK STATE MACHINE
# ===================================================================

class InputJack:
    def __init__(self, io_id: str, module):
        self.io_id = io_id
        self.module = module
        self.state = InputState.IIdleDisconnected
        self.pending_initiator = None  # (src_mod, src_io, payload)
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

    def short_press(self):
            if self.state == InputState.IIdleDisconnected:
                self._send_compatible()
                self.state = InputState.ISelfCompatible
                self.module._notify_self_compatible(self.io_id)
            elif self.state == InputState.IIdleConnected:
                self._send_reveal()
            elif self.state == InputState.IPending:
                self._accept_connection()
            self._set_led()

    def long_press(self):
        if self.state == InputState.IIdleConnected:
            self._disconnect()
        elif self.state == InputState.ISelfCompatible:
            self.state = InputState.IIdleDisconnected
            self.module._broadcast_cancel()  # abort our own compatible mode
        self._set_led()

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

    def _accept_connection(self):
        if not self.pending_initiator:
            return
        src_mod, src_io, payload = self.pending_initiator
        group = payload.get("group")
        offset = payload.get("offset", 0)
        block_size = payload.get("block_size", 96)
        rec = ConnectionRecord(f"{src_mod}:{src_io}", group, offset, block_size)
        self.module.input_connections[self.io_id] = rec
        self.state = InputState.IIdleConnected
        self.module._start_receiver(self.io_id, group, offset, block_size)

        connect_msg = ProtocolMessage(ProtocolMessageType.CONNECT.value, src_mod, io_id=src_io)
        self.module.sock.sendto(connect_msg.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))

        logger.info(f"[{self.module.module_id}] Connected {self.io_id} ← {src_mod}:{src_io}")
        self.pending_initiator = None
        self._set_led()

    def _disconnect(self):
        rec = self.module.input_connections.get(self.io_id)
        if rec:
            self.module.input_connections[self.io_id] = None
            self.state = InputState.IIdleDisconnected
            self.module._stop_receiver(self.io_id)  # implement if needed
            logger.info(f"[{self.module.module_id}] Disconnected {self.io_id}")
        self._set_led()

    def on_initiate(self, msg: ProtocolMessage):
        if msg.module_id == self.module.module_id:
            return
        payload = msg.payload or {}
        src_type = payload.get("type", "unknown")
        my_type = self.module.inputs[self.io_id].get("type", "unknown")

        if my_type != src_type:
            if self.state == InputState.IPending:
                self.state = InputState.IOtherPending
            return

        # compatible
        if self.state in (InputState.IIdleDisconnected, InputState.IOtherCompatible):
            self.state = InputState.IPending
            self.pending_initiator = (msg.module_id, msg.io_id, payload)
        elif self.state == InputState.IPending:
            if (msg.module_id, msg.io_id) == self.pending_initiator[:2]:
                self.state = InputState.IPendingSame
            # else stay IPending
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
                
    def _refresh_gui_from_controls(self):
        """
        Universal restore method — works for every module type.
        Called on module creation and on patch restore.
        """
        # ── 1. Restore control values (sliders/knobs) ───────────────────────
        # All modules have self.controls and self.control_vars (or similar)
        control_vars = getattr(self, "control_vars", {})
        for ctrl_id, var in control_vars.items():
            if ctrl_id in self.controls:
                # This triggers Tkinter variable → GUI update
                var.set(self.controls[ctrl_id])

        # ── 2. Restore connection LEDs (respect pending states) ─────────────
        # INPUTS
        for io_id, jack in self.input_jacks.items():
            rec = self.input_connections.get(io_id)
            if rec:
                # Connected → BLINK_RAPID, but don't override active pending modes
                if jack.state not in (
                    InputState.IPending,
                    InputState.IPendingSame,
                    InputState.ISelfCompatible,
                    InputState.IOtherCompatible,
                    InputState.IOtherPending
                ):
                    self._queue_led_update(io_id, LedState.BLINK_RAPID)
            else:
                # Disconnected → OFF, unless pending/compatible
                if jack.state not in (
                    InputState.IPending,
                    InputState.IPendingSame,
                    InputState.ISelfCompatible,
                    InputState.IOtherCompatible
                ):
                    self._queue_led_update(io_id, LedState.OFF)

        # OUTPUTS
        for io_id, jack in self.output_jacks.items():
            if jack.state in (OutputState.OIdle, OutputState.OCompatible):
                self._queue_led_update(io_id, LedState.SOLID)
            # OSelfPending → leave blinking (correct)
            # OOtherPending / ONotCompatible → leave OFF (correct)
            
    def get_state(self) -> Dict:
            """Return current module state for patch save"""
            connections = {}
            for io_id, rec in self.input_connections.items():
                if rec:
                    connections[io_id] = {
                        "src": rec.src,
                        "group": rec.mcast_group,
                        "offset": rec.block_offset,
                        "block_size": rec.block_size,
                    }
            return {
                "controls": self.controls.copy(),
                "connections": connections
            }
            
    def _stop_receiver(self, io_id: str):
            pass