import json
import logging
from typing import Dict, Any
from base_module import BaseModule, ProtocolMessage, ProtocolMessageType, CONTROL_MULTICAST, UDP_CONTROL_PORT, LedState

logger = logging.getLogger(__name__)

class PatchProtocol:
    def handle_msg(self, msg: ProtocolMessage):
        if msg.type == ProtocolMessageType.CAPABILITIES_INQUIRY.value:
            caps = self.get_capabilities()  # Delegate to base
            resp = ProtocolMessage(ProtocolMessageType.CAPABILITIES_RESPONSE.value, self.module_id, payload=caps)
            self.sock.sendto(resp.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
            logger.info(f"{self.module_id}: Sent capabilities")
        elif msg.type == ProtocolMessageType.STATE_INQUIRY.value:
            state = self.get_state()  # Delegate to base
            resp = ProtocolMessage(ProtocolMessageType.STATE_RESPONSE.value, self.module_id, payload=state)
            self.sock.sendto(resp.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
            logger.info(f"{self.module_id}: Sent state")
        elif msg.type == ProtocolMessageType.PATCH_RESTORE.value:
            self.restore_patch(msg.payload)
            if self.root:
                self.root.after(100, self._sync_ui)
        super().handle_msg(msg)

    def _sync_ui(self):
        self._update_display()

    def restore_patch(self, data: bytes or Dict):
        if isinstance(data, bytes):
            data = json.loads(data)
        target = data.pop('target_mod', None)
        if target is not None and target != self.module_id:
            return
        # No lock: Atomic
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
        for io in self.inputs:
            state = LedState.BLINK_RAPID if self.inputs.get(io, {}).get("src") else LedState.OFF
            self._queue_led_update(io, state)
        for io in self.outputs:
            self._queue_led_update(io, LedState.SOLID)
        logger.info(f"{self.module_id}: Patch restored")