# connection_protocol.py — FINAL CSV-ACCURATE, WITH COMPATIBLE SUPPORT
import logging
from enum import Enum, auto
from typing import Optional
from base_module import (
    ProtocolMessage, ProtocolMessageType, CONTROL_MULTICAST, UDP_CONTROL_PORT,
    LedState, ConnectionRecord
)

logger = logging.getLogger(__name__)

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

class ConnectionProtocol:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.output_states: dict[str, OutputState] = {}
        self.input_states: dict[str, InputState] = {}
        self.input_connections: dict[str, Optional[ConnectionRecord]] = {}
        self.pending_initiator: Optional[tuple] = None
        self.compatible_input: Optional[str] = None  # Track self-compatible input

    def _init_connection_states(self):
        self.output_states = {io: OutputState.OIdle for io in self.outputs}
        self.input_states = {io: InputState.IIdleDisconnected for io in self.inputs}
        self.input_connections = {io: None for io in self.inputs}
        self.pending_initiator = None
        self.compatible_input = None

    def _sync_initial_leds(self):
        self._init_connection_states()
        for io_id in self.outputs:
            self._queue_led_update(io_id, LedState.SOLID)
        for io_id in self.inputs:
            if self.input_connections[io_id]:
                self.input_states[io_id] = InputState.IIdleConnected
                self._queue_led_update(io_id, LedState.BLINK_RAPID)
            else:
                self.input_states[io_id] = InputState.IIdleDisconnected
                self._queue_led_update(io_id, LedState.OFF)

    def _revert_all_pending(self):
        # Revert inputs
        for io_id in self.inputs:
            if self.input_states[io_id] in (InputState.IPending, InputState.IPendingSame, InputState.IOtherPending, InputState.ISelfCompatible):
                self.input_states[io_id] = InputState.IIdleDisconnected
                self._queue_led_update(io_id, LedState.OFF)
        # Revert outputs
        for io_id in self.outputs:
            if self.output_states[io_id] in (OutputState.OSelfPending, OutputState.OOtherPending, OutputState.OCompatible, OutputState.ONotCompatible):
                self.output_states[io_id] = OutputState.OIdle
                self._queue_led_update(io_id, LedState.SOLID)
        self.pending_initiator = None
        self.compatible_input = None
        logger.info(f"[{self.module_id}] All pending/compatible states cleared")

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
        elif msg.type == ProtocolMessageType.STATE_INQUIRY.value and msg.module_id == "mcu":
            # CRITICAL: Respond to save patch
            state = self.get_state()
            resp = ProtocolMessage(
                ProtocolMessageType.STATE_RESPONSE.value,
                self.module_id,
                payload=state
            )
            self.sock.sendto(resp.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
            logger.info(f"[{self.module_id}] Sent STATE_RESPONSE for save")

        super().handle_msg(msg)  # Let PatchProtocol see PATCH_RESTORE

    def _handle_initiate(self, msg: ProtocolMessage):
        if msg.module_id == self.module_id:
            return

        payload = msg.payload or {}
        group = payload.get("group")
        io_type = payload.get("type")

        self.pending_initiator = (msg.module_id, msg.io_id, payload)

        # Update inputs per CSV: compatible → IPending (SOLID), else IOtherPending (OFF)
        for io_id, info in self.inputs.items():
            compatible = (info.get("type") == io_type and info.get("group") == group)
            current = self.input_states[io_id]
            if compatible:
                if current in (InputState.IIdleDisconnected, InputState.ISelfCompatible):
                    self.input_states[io_id] = InputState.IPending
                    self._queue_led_update(io_id, LedState.SOLID)
                elif current == InputState.IIdleConnected:
                    pass  # Temporarily OFF for celebration (CSV note)
            else:
                if current in (InputState.IIdleDisconnected, InputState.IPending, InputState.ISelfCompatible):
                    self.input_states[io_id] = InputState.IOtherPending
                    self._queue_led_update(io_id, LedState.OFF)

        # Update outputs: all non-self to OOtherPending (OFF)
        for io_id in self.outputs:
            if self.output_states[io_id] == OutputState.OIdle:
                self.output_states[io_id] = OutputState.OOtherPending
                self._queue_led_update(io_id, LedState.OFF)

    def _handle_compatible(self, msg: ProtocolMessage):
        # Response to COMPATIBLE: Check if our outputs match the input's type/group
        if msg.module_id == self.module_id:
            return
        payload = msg.payload or {}
        req_group = payload.get("group")
        req_type = payload.get("type")
        for io_id, info in self.outputs.items():
            compatible = (info.get("type") == req_type and info.get("group") == req_group)
            current = self.output_states[io_id]
            if compatible:
                if current in (OutputState.OIdle, OutputState.OOtherPending):
                    self.output_states[io_id] = OutputState.OCompatible
                    self._queue_led_update(io_id, LedState.SOLID)  # Stay SOLID
            else:
                if current in (OutputState.OIdle, OutputState.OCompatible):
                    self.output_states[io_id] = OutputState.ONotCompatible
                    self._queue_led_update(io_id, LedState.OFF)  # Dim incompatible

    def _handle_connect(self, msg: ProtocolMessage):
        if msg.module_id != self.module_id:
            return
        io_id = msg.io_id
        if io_id in self.outputs:
            # Celebration: 3x rapid BLINK_RAPID/SOLID
            for i in range(6):
                self.root.after(100 * i, lambda ii=io_id, on=(i % 2 == 0):
                    self._queue_led_update(ii, LedState.BLINK_RAPID if on else LedState.SOLID))
            self.root.after(600, lambda: self._queue_led_update(io_id, LedState.SOLID))
            self.output_states[io_id] = OutputState.OIdle

    def _handle_cancel(self, msg: ProtocolMessage):
        self._revert_all_pending()

    def _handle_show_connected(self, msg: ProtocolMessage):
        if msg.module_id != self.module_id or msg.io_id not in self.outputs:
            return
        io_id = msg.io_id
        # Flash 3x: BLINK_RAPID/OFF
        for i in range(6):
            self.root.after(100 * i, lambda ii=io_id, on=(i % 2 == 0):
                self._queue_led_update(ii, LedState.BLINK_RAPID if on else LedState.OFF))
        self.root.after(600, lambda: self._queue_led_update(io_id, LedState.SOLID))

    # User Actions
    def initiate_connect(self, io_id: str):  # Output short press
        state = self.output_states.get(io_id, OutputState.OIdle)
        if state == OutputState.OSelfPending:
            # CSV: Send CANCEL, back to OIdle
            cancel_msg = ProtocolMessage(ProtocolMessageType.CANCEL.value, self.module_id)
            self.sock.sendto(cancel_msg.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
            self._revert_all_pending()
            return
        if state not in (OutputState.OIdle, OutputState.OCompatible):
            return  # Ignore if not valid (e.g., OOtherPending)

        info = self.outputs[io_id]
        payload = {"group": info.get("group", self.mcast_group), "type": info.get("type", "unknown"), "offset": 0, "block_size": 96}
        msg = ProtocolMessage(ProtocolMessageType.INITIATE.value, self.module_id, self.type, io_id, payload)
        self.sock.sendto(msg.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))

        self.output_states[io_id] = OutputState.OSelfPending
        self._queue_led_update(io_id, LedState.BLINK_SLOW)
        logger.info(f"[{self.module_id}] INITIATE from {io_id}")

    def connect_input(self, io_id: str):  # Input short press
        state = self.input_states.get(io_id)
        if state == InputState.ISelfCompatible:
            # Enter initiate_compatible: slow blink this input, dim incompatible outputs
            self.compatible_input = io_id
            self.input_states[io_id] = InputState.ISelfCompatible
            self._queue_led_update(io_id, LedState.BLINK_SLOW)
            info = self.inputs[io_id]
            payload = {"group": info.get("group", ""), "type": info.get("type", "unknown")}
            msg = ProtocolMessage(ProtocolMessageType.COMPATIBLE.value, self.module_id, self.type, io_id, payload)
            self.sock.sendto(msg.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
            logger.info(f"[{self.module_id}] COMPATIBLE from input {io_id}")
            return
        if state != InputState.IPending or not self.pending_initiator:
            return  # Not ready to connect

        # Complete connection per CSV
        src_mod, src_io, payload = self.pending_initiator
        group = payload.get("group")
        offset = payload.get("offset", 0)
        block_size = payload.get("block_size", 96)
        rec = ConnectionRecord(f"{src_mod}:{src_io}", group, offset, block_size)
        self.input_connections[io_id] = rec
        self.input_states[io_id] = InputState.IIdleConnected  # To IPendingSame if repeat, but simplify
        self._queue_led_update(io_id, LedState.BLINK_RAPID)
        self._start_receiver(io_id, group, offset, block_size)

        connect_msg = ProtocolMessage(ProtocolMessageType.CONNECT.value, src_mod, io_id=src_io)
        self.sock.sendto(connect_msg.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))

        cancel_msg = ProtocolMessage(ProtocolMessageType.CANCEL.value, self.module_id)
        self.sock.sendto(cancel_msg.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
        self._revert_all_pending()
        logger.info(f"[{self.module_id}] Connected {io_id} ← {src_mod}:{src_io}")

    def long_press_input(self, io_id: str):  # Disconnect connected input
        if self.input_states.get(io_id) != InputState.IIdleConnected:
            return
        rec = self.input_connections.get(io_id)
        if not rec:
            return

        cancel_msg = ProtocolMessage(ProtocolMessageType.CANCEL.value, self.module_id)
        self.sock.sendto(cancel_msg.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))

        self.input_connections[io_id] = None
        self.input_states[io_id] = InputState.IIdleDisconnected
        self._queue_led_update(io_id, LedState.OFF)
        logger.info(f"[{self.module_id}] Disconnected {io_id}")
        
    def _ensure_io_defs(self):
        """Ensure inputs/outputs are visible even if set after super().__init__()"""
        # This is called before any protocol logic that needs self.inputs/self.outputs
        pass  # No-op — just a hook