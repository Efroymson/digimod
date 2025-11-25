# patch_protocol.py — COMPLETE, FINAL, WORKING
import json
import logging
from typing import Dict, Any

from base_module import (
    ProtocolMessage,
    ProtocolMessageType,
    CONTROL_MULTICAST,
    UDP_CONTROL_PORT,
    LedState,
)
from connection_record import ConnectionRecord

logger = logging.getLogger(__name__)

class PatchProtocol:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def handle_msg(self, msg: ProtocolMessage):
        if msg.type == ProtocolMessageType.CAPABILITIES_INQUIRY.value:
            caps = self.get_capabilities()
            resp = ProtocolMessage(ProtocolMessageType.CAPABILITIES_RESPONSE.value, self.module_id, payload=caps)
            self.sock.sendto(resp.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))

        elif msg.type == ProtocolMessageType.STATE_INQUIRY.value:
            state = self.get_state()
            resp = ProtocolMessage(ProtocolMessageType.STATE_RESPONSE.value, self.module_id, payload=state)
            self.sock.sendto(resp.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))

        elif msg.type == ProtocolMessageType.PATCH_RESTORE.value:
            payload = msg.payload
            target = payload.get("target_mod")
            if target and target != self.module_id:
                return

            data = payload.get("payload", payload) if isinstance(payload, dict) else payload
            if isinstance(data, dict):
                self.restore_patch(data)

            if self.root:
                self.root.after(150, self._full_ui_refresh)

        super().handle_msg(msg)

    def _full_ui_refresh(self):
        # Full reset
        self._init_connection_states()
        self._sync_initial_leds()

        # Update controls via module-specific method (osc_module.py has this)
        if hasattr(self, "_refresh_gui_from_controls"):
            self._refresh_gui_from_controls()

        # Re-apply connections: state + LED + receiver
        for io_id, rec in self.input_connections.items():
            if rec:
                self.input_states[io_id] = "IIdleConnected"
                self._queue_led_update(io_id, LedState.BLINK_RAPID)
                self._start_receiver(io_id, rec.mcast_group, rec.block_offset, rec.block_size)
                logger.info(f"[{self.module_id}] Restored connection {io_id} ← {rec.src}")
            else:
                self.input_states[io_id] = "IIdleDisconnected"
                self._queue_led_update(io_id, LedState.OFF)

        # Outputs solid
        for io_id in self.output_states:
            self.output_states[io_id] = "OIdle"
            self._queue_led_update(io_id, LedState.SOLID)

        if hasattr(self, "_update_display"):
            self._update_display()
        if self.root:
            self.root.update_idletasks()

    def get_state(self) -> Dict:
        connections = {}
        for io, rec in self.input_connections.items():
            if rec:
                connections[io] = {
                    "src": rec.src,
                    "group": rec.mcast_group,
                    "offset": rec.block_offset,
                    "block_size": rec.block_size,
                }
        return {"controls": self.controls.copy(), "connections": connections}

    def restore_patch(self, data: Dict):
        if not isinstance(data, dict):
            return
        if data.get("target_mod") and data["target_mod"] != self.module_id:
            return

        # Restore controls
        for k, v in data.get("controls", {}).items():
            if k in self.control_ranges:
                lo, hi = self.control_ranges[k]
                self.controls[k] = max(lo, min(hi, float(v)))

        # Clear connections
        for io in self.inputs:
            self.input_connections[io] = None
            self.input_states[io] = "IIdleDisconnected"
            self._queue_led_update(io, LedState.OFF)

        # Restore connections
        for io, info in data.get("connections", {}).items():
            if io not in self.inputs or not info:
                continue
            rec = ConnectionRecord(
                src=info["src"],
                mcast_group=info["group"],
                block_offset=info.get("offset", 0),
                block_size=info.get("block_size", 96),
            )
            self.input_connections[io] = rec