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
            for io_id, rec in self.input_connections.items():
                if rec:
                    connections[io_id] = {
                        "src": rec.src,
                        "src_io": rec.src_io,               # <-- add this line
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

        # ---- 1. Controls ------------------------------------------------
        for k, v in data.get("controls", {}).items():
            if k in self.control_ranges:
                lo, hi = self.control_ranges[k]
                self.controls[k] = max(lo, min(hi, float(v)))

        # ---- 2. Wipe old connections ------------------------------------
        for io in list(self.input_connections.keys()):
            if io in self.input_jacks:
                jack = self.input_jacks[io]
                jack.state = InputState.IIdleDisconnected
                jack._set_led()
            self._stop_receiver(io) if hasattr(self, "_stop_receiver") else None
            self.input_connections[io] = None

        # ---- 3. Restore saved connections -------------------------------
        for io, info in data.get("connections", {}).items():
            if io not in self.inputs or not info:
                continue

            # Re-create the ConnectionRecord exactly as during live connect
            rec = ConnectionRecord(
                src=info["src"],
                src_io=info.get("src_io", ""),          # <-- needed for REVEAL
                mcast_group=info["group"],
                block_offset=info.get("offset", 0),
                block_size=info.get("block_size", 96),
            )
            self.input_connections[io] = rec

            # Start receiving again
            self._start_receiver(io, rec.mcast_group, rec.block_offset, rec.block_size)

            # <<< THIS IS THE CRUCIAL PART THAT WAS MISSING >>>
            if io in self.input_jacks:
                jack = self.input_jacks[io]
                jack.connected_src       = info["src"]
                jack.connected_src_io     = info.get("src_io", "")
                jack.connected_mcast      = info["group"]
                jack.connected_offset     = info.get("offset", 0)
                jack.connected_block_size = info.get("block_size", 96)
                jack.state = InputState.IIdleConnected
                jack._set_led()                     # ← now BLINK_RAPID immediately
            # <<< END CRUCIAL PART >>>

        # ---- 4. Final GUI refresh ---------------------------------------
        if self.root:
            self.root.after(100, self._refresh_gui_from_controls)
            
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
        # Force Tkinter to update all widgets immediately
        if hasattr(self, "root") and self.root:
            self.root.update_idletasks()
            
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