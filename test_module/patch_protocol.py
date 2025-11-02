import json
import logging
from typing import Dict, Any
from base_module import BaseModule, ProtocolMessage, ProtocolMessageType, CONTROL_MULTICAST, UDP_CONTROL_PORT, LedState

logger = logging.getLogger(__name__)

class PatchProtocol:
    # Stateless mixin - no __init__

    def handle_msg(self, msg: ProtocolMessage):
        with self.lock:
            if msg.type == ProtocolMessageType.CAPABILITIES_INQUIRY.value:
                caps = self.get_capabilities()
                resp = ProtocolMessage(ProtocolMessageType.CAPABILITIES_RESPONSE.value, self.module_id, payload=caps)
                self.sock.sendto(resp.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
                logger.info(f"{self.module_id}: Sent capabilities")
            elif msg.type == ProtocolMessageType.STATE_INQUIRY.value:
                state = self.get_state()
                resp = ProtocolMessage(ProtocolMessageType.STATE_RESPONSE.value, self.module_id, payload=state)
                self.sock.sendto(resp.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
                logger.info(f"{self.module_id}: Sent state")
            elif msg.type == ProtocolMessageType.PATCH_RESTORE.value:
                self.restore_patch(msg.payload)
                # Sync UI after restore (event-driven)
                if self.root:
                    self.root.after(100, self._sync_ui)
        super().handle_msg(msg)

    def _sync_ui(self):
        self._update_display()  # Drain LED queue + set vars silently

    def get_capabilities(self) -> Dict:
        return {
            "name": self.module_id,
            "type": self.type,
            "controls": [{"id": k, "range": v, "default": self.controls.get(k, v[0] if v else 0)} for k, v in self.control_ranges.items()],
            "inputs": [{"id": k, "type": v['type']} for k, v in self.inputs.items()],
            "outputs": [{"id": k, "type": v['type']} for k, v in self.outputs.items()]
        }

    def get_state(self) -> Dict:
        return {
            "controls": self.controls.copy(),
            "inputs": {k: {"src": v.get("src"), "group": v.get("group")} for k, v in self.inputs.items()},
            "outputs": self.outputs.copy()
        }

    def restore_patch(self, data: bytes or Dict):
        if isinstance(data, bytes):
            data = json.loads(data)
        with self.lock:
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
            self.led_states = {io: LedState.SOLID if self.inputs.get(io, {}).get("src") else LedState.OFF for io in self.inputs}
            self.led_states.update({io: LedState.SOLID for io in self.outputs if self.outputs.get(io)})
        logger.info(f"{self.module_id}: Patch restored")