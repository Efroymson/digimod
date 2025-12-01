# patch_manager.py — lives only on the MCU
import json
from connection_protocol import ProtocolMessage, ProtocolMessageType, UDP_CONTROL_PORT

class PatchManager:
    def __init__(self, sock, log_callback=None):
        self.sock = sock
        self.log = log_callback or print
        self.patches = [[] for _ in range(8)]  # 8 slots

    def save_patch(self, slot: int, module_states: list):
        self.patches[slot] = module_states
        self.log(f"Patch saved to slot {slot} ({len(module_states)} modules)")

    def load_patch(self, slot: int):
        patch = self.patches[slot]
        if not patch:
            self.log("Empty slot")
            return

        for state in patch:
            ip = state["unicast_ip"]
            msg = ProtocolMessage(
                ProtocolMessageType.PATCH_RESTORE.value,
                "mcu",
                payload={"state": state}
            )
            self.sock.sendto(msg.pack(), (ip, UDP_CONTROL_PORT))
            self.log(f"Unicast restore → {state['module_id']} @ {ip}")