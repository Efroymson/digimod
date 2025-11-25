# connection_protocol.py — FINAL STATE MACHINE VERSION (based on your CSV)
import logging
from enum import Enum, auto
from typing import Dict, Optional, Callable, Any
from base_module import (
    ProtocolMessage, ProtocolMessageType, CONTROL_MULTICAST, UDP_CONTROL_PORT, LedState
)

logger = logging.getLogger(__name__)

# === STATES ===
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
    IPendingSame = auto()  # New state from your CSV

# === MAIN CLASS ===
class ConnectionProtocol:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.output_states: Dict[str, OutputState] = {}
        self.input_states: Dict[str, InputState] = {}
        self.input_connections: Dict[str, Any] = {}
        self.pending_initiator: Optional[tuple] = None
        self.pending_timeout_id = None

    def _init_connection_states(self):
        self.output_states = {io: OutputState.OIdle for io in self.outputs}
        self.input_states = {io: InputState.IIdleDisconnected for io in self.inputs}
        self.input_connections = {io: None for io in self.inputs}

    def _sync_initial_leds(self):
        self._init_connection_states()
        for io_id in self.outputs:
            self._queue_led_update(io_id, LedState.SOLID)
        for io_id in self.inputs:
            if self.input_connections[io_id]:
                self.input_states[io_id] = InputState.IIdleConnected
                self._queue_led_update(io_id, LedState.BLINK_RAPID)
            else:
                self._queue_led_update(io_id, LedState.OFF)

    def handle_msg(self, msg: ProtocolMessage):
        if msg.type == ProtocolMessageType.INITIATE.value:
            self._handle_initiate(msg)
        elif msg.type == ProtocolMessageType.CONNECT.value:
            self._handle_connect(msg)
        elif msg.type == ProtocolMessageType.CANCEL.value:
            self._handle_cancel(msg)
        elif msg.type == ProtocolMessageType.COMPATIBLE.value:
            self._handle_compatible(msg)
        elif msg.type == ProtocolMessageType.SHOW_CONNECTED.value:
            self._handle_show_connected(msg)
        super().handle_msg(msg)

    # === STATE TRANSITIONS ===
    def _handle_initiate(self, msg: ProtocolMessage):
        if msg.module_id == self.module_id:
            return

        payload = msg.payload or {}
        group = payload.get("group", "")
        io_type = payload.get("type", "")

        self.pending_initiator = (msg.module_id, msg.io_id, payload)
        if self.pending_timeout_id:
            self.root.after_cancel(self.pending_timeout_id)
        self.pending_timeout_id = self.root.after(5000, self._revert_all_pending)

        logger.info(f"[{self.module_id}] INITIATE from {msg.module_id}:{msg.io_id} type={io_type} group={group}")

        # Update inputs
        for io_id, info in self.inputs.items():
            is_compatible = (info.get("type") == io_type and info.get("group") == group)
            current = self.input_states[io_id]

            if is_compatible:
                if current == InputState.IIdleDisconnected:
                    self.input_states[io_id] = InputState.IPending
                    self._queue_led_update(io_id, LedState.SOLID)
                elif current == InputState.IIdleConnected:
                    self.input_states[io_id] = InputState.IPendingSame
                    self._queue_led_update(io_id, LedState.BLINK_SLOW)
            else:
                if current not in (InputState.IIdleConnected, InputState.IPendingSame):
                    self.input_states[io_id] = InputState.IOtherPending
                    self._queue_led_update(io_id, LedState.OFF)

        # Update outputs (simple: all non-initiator go dark)
        for io_id in self.output_states:
            if (msg.module_id, io_id) == (self.module_id, io_id):  # this module's output
                self.output_states[io_id] = OutputState.OSelfPending
                self._queue_led_update(io_id, LedState.BLINK_SLOW)
            else:
                self.output_states[io_id] = OutputState.OOtherPending
                self._queue_led_update(io_id, LedState.OFF)

    def _handle_connect(self, msg: ProtocolMessage):
        io_id = msg.io_id
        if io_id not in self.inputs:
            return
        if self.input_states[io_id] not in (InputState.IPending, InputState.IPendingSame):
            return

        src_mod, src_io, payload = self.pending_initiator
        group = payload.get("group", "")
        offset = payload.get("offset", 0)
        block_size = payload.get("block_size", 96)

        from connection_record import ConnectionRecord
        self.input_connections[io_id] = ConnectionRecord(
            f"{src_mod}:{src_io}", group, offset, block_size
        )
        self.input_states[io_id] = InputState.IIdleConnected
        self._queue_led_update(io_id, LedState.BLINK_RAPID)
        self._start_receiver(io_id, group, offset, block_size)

        if self.pending_timeout_id:
            self.root.after_cancel(self.pending_timeout_id)
            self.pending_timeout_id = None

        logger.info(f"[{self.module_id}] CONNECTED {io_id} ← {src_mod}:{src_io}")

    def _revert_all_pending(self):
        for io_id in self.inputs:
            if self.input_states[io_id] in (InputState.IPending, InputState.IPendingSame):
                self.input_states[io_id] = InputState.IIdleDisconnected
                self._queue_led_update(io_id, LedState.OFF)
        for io_id in self.outputs:
            if self.output_states[io_id] in (OutputState.OSelfPending, OutputState.OOtherPending):
                self.output_states[io_id] = OutputState.OIdle
                self._queue_led_update(io_id, LedState.SOLID)
        self.pending_timeout_id = None
        logger.info(f"[{self.module_id}] Global pending timeout — all reverted")

    # Keep your existing connect_input, initiate_connect, long_press_input
    # They are correct.

    # REQUIRED METHODS — THESE WERE MISSING!
    def connect_input(self, io_id: str):
        """User short-pressed an input jack — accept pending connection"""
        if self.input_states.get(io_id) != InputState.IPending:
            return
        if not self.pending_initiator:
            return
        src_mod, src_io, payload = self.pending_initiator
        group = payload.get("group", "")
        offset = payload.get("offset", 0)
        block_size = payload.get("block_size", 96)

        rec = ConnectionRecord(f"{src_mod}:{src_io}", group, offset, block_size)
        self.input_connections[io_id] = rec
        self.input_states[io_id] = InputState.IIdleConnected
        self._queue_led_update(io_id, LedState.BLINK_RAPID)
        self._start_receiver(io_id, group, offset, block_size)

        if self.pending_timeout_id:
            self.root.after_cancel(self.pending_timeout_id)
            self.pending_timeout_id = None

        logger.info(f"[{self.module_id}] Connected {io_id} ← {src_mod}:{src_io}")

    def long_press_input(self, io_id: str):
        """User long-pressed a connected input — disconnect"""
        if self.input_states.get(io_id) != InputState.IIdleConnected:
            return
        rec = self.input_connections.get(io_id)
        if not rec:
            return
        src_mod, src_io = rec.src.split(":")
        payload = {"output_mod": src_mod, "output_io": src_io}
        msg = ProtocolMessage(ProtocolMessageType.CANCEL.value, self.module_id, self.type, io_id, payload)
        self.sock.sendto(msg.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
        self.input_connections[io_id] = None
        self.input_states[io_id] = InputState.IIdleDisconnected
        self._queue_led_update(io_id, LedState.OFF)
        logger.info(f"[{self.module_id}] Disconnected {io_id}")

    def initiate_connect(self, io_id: str):
        info = self.outputs.get(io_id)
        if not info:
            return

        payload = {
            "group": info.get("group", self.mcast_group),
            "type": info.get("type", "unknown"),
            "offset": 0,
            "block_size": 96,
        }

        msg = ProtocolMessage(ProtocolMessageType.INITIATE.value, self.module_id, self.type, io_id, payload)
        self.sock.sendto(msg.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))

        self.output_states[io_id] = OutputState.OSelfPending
        self._queue_led_update(io_id, LedState.BLINK_SLOW)

        # Start global pending timeout
        if self.pending_timeout_id:
            self.root.after_cancel(self.pending_timeout_id)
        self.pending_timeout_id = self.root.after(5000, self._revert_pending_inputs)

        logger.info(f"[{self.module_id}] Initiating connection from {io_id}")

    