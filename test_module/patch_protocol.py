# patch_protocol.py — FINAL, RESTORES BOTH CONTROLS AND CONNECTIONS
import json
import logging
from typing import Dict
from base_module import (
    ProtocolMessage, ProtocolMessageType, CONTROL_MULTICAST, UDP_CONTROL_PORT,
    LedState, ConnectionRecord
)
from connection_protocol import InputState, OutputState   

logger = logging.getLogger(__name__)

class PatchProtocol:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def handle_msg(self, msg: ProtocolMessage):
        if msg.type == ProtocolMessageType.PATCH_RESTORE.value:
            payload = msg.payload
            target = payload.get("target_mod")
            if target and target != self.module_id:
                return

            data = payload.get("payload", payload) if isinstance(payload, dict) else payload
            if isinstance(data, dict):
                self.restore_patch(data)
                if self.root:
                    self.root.after(100, self._full_ui_refresh)

        super().handle_msg(msg)

    
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

        # 1. Restore controls
        for k, v in data.get("controls", {}).items():
            if k in self.control_ranges:
                lo, hi = self.control_ranges[k]
                self.controls[k] = max(lo, min(hi, float(v)))

        # 2. WIPE ALL CONNECTIONS — GOLDEN RULE
        for io in list(self.input_connections.keys()):
            self.input_connections[io] = None

        # 3. Re-apply saved connections
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

        # 4. ONE AND ONLY ONE CALL — universal, correct, safe
        if self.root:
            self.root.after(50, self._refresh_gui_from_controls)