import tkinter as tk
from tkinter import ttk, Text, Scrollbar
import json
import threading
import time
import logging
import socket
import struct
from typing import Dict, List, Tuple
import queue
from osc_module import OscModule
from lfo_module import LfoModule
from audio_out_module import AudioOutModule
from graph_viewer import PatchViewer
from base_module import ProtocolMessage, ProtocolMessageType, CONTROL_MULTICAST, UDP_CONTROL_PORT, RECV_TIMEOUT

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class MainApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("DMS Simulator")
        self.root.geometry("400x300")
        self.modules: Dict[str, 'BaseModule'] = {}
        self.module_states: Dict[str, Dict] = {}
        self.saved_patches: Dict[int, Dict[str, Dict]] = {0: {}, 1: {}, 2: {}, 3: {}, 4: {}}
        self.current_slot = tk.IntVar(value=0)
        self.deferred_modules: List[Tuple[str, str]] = []
        self.viewer: PatchViewer = None
        self.log_queue = queue.Queue()
        self._setup_gui()
        self._setup_mcu()
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def _setup_mcu(self):
        self.mcu_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.mcu_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.mcu_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        self.mcu_sock.bind(('', UDP_CONTROL_PORT))
        mreq = struct.pack("4sl", socket.inet_aton(CONTROL_MULTICAST), socket.INADDR_ANY)
        self.mcu_sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        self.mcu_sock.settimeout(RECV_TIMEOUT)
        self._mcu_listener_thread = threading.Thread(target=self._mcu_listener, daemon=True)
        self._mcu_listener_thread.start()

    def _mcu_listener(self):
        while True:
            try:
                data, addr = self.mcu_sock.recvfrom(1024)
                msg = ProtocolMessage.unpack(data)
                if msg.type == ProtocolMessageType.CAPABILITIES_RESPONSE.value:
                    payload_str = json.dumps(msg.payload, indent=2) + "\n\n"
                    self.log_queue.put(f"Received {ProtocolMessageType(msg.type).name} from {msg.module_id}:\n{payload_str}")
                elif msg.type == ProtocolMessageType.STATE_RESPONSE.value:
                    self.module_states[msg.module_id] = msg.payload
                    payload_str = json.dumps(msg.payload, indent=2) + "\n\n"
                    self.log_queue.put(f"Received {ProtocolMessageType(msg.type).name} from {msg.module_id}:\n{payload_str}")
                elif msg.type in [ProtocolMessageType.INITIATE.value, ProtocolMessageType.CONNECT.value, ProtocolMessageType.CANCEL.value]:
                    group = msg.payload.get('group', 'N/A') if isinstance(msg.payload, dict) else 'N/A'
                    self.log_queue.put(f"Received {ProtocolMessageType(msg.type).name} from {msg.module_id}:{msg.io_id} (group: {group})\n")
            except socket.timeout:
                pass
            except Exception as e:
                logger.warning(f"MCU listener error: {e}")

    def _setup_gui(self):
        slot_frame = ttk.Frame(self.root)
        slot_frame.pack(pady=5)
        ttk.Label(slot_frame, text="Patch Slot:").pack(side=tk.LEFT)
        self.slot_scale = ttk.Scale(slot_frame, from_=0, to=4, orient="horizontal",
                                    variable=self.current_slot, length=100, command=self._snap_slot)
        self.slot_scale.pack(side=tk.LEFT, padx=5)
        ttk.Label(slot_frame, textvariable=self.current_slot).pack(side=tk.LEFT, padx=5)

        ttk.Button(self.root, text="Add OSC", command=self.add_osc).pack(pady=5)
        ttk.Button(self.root, text="Add LFO", command=self.add_lfo).pack(pady=5)
        ttk.Button(self.root, text="Add Audio Out", command=self.add_audio_out).pack(pady=5)
        ttk.Button(self.root, text="Auto-Launch Test Rig", command=self.launch_test_rig).pack(pady=5)
        ttk.Button(self.root, text="Inquiry Caps", command=self.inquiry_caps).pack(pady=5)
        ttk.Button(self.root, text="Inquiry State", command=self.inquiry_state).pack(pady=5)
        ttk.Button(self.root, text="Save Patch", command=self.save_patch).pack(pady=5)
        ttk.Button(self.root, text="Restore Patch", command=self.restore_patch).pack(pady=5)
        ttk.Button(self.root, text="Show Graph", command=self.show_graph).pack(pady=5)

        scrollbar = Scrollbar(self.root)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text = Text(self.root, height=15, yscrollcommand=scrollbar.set, wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        scrollbar.config(command=self.log_text.yview)
        self.log_text.insert(tk.END, "DMS Simulator Ready. Add modules to start.\n")
        # Start log drain
        self.root.after(50, self._periodic_log_drain)

    def _periodic_log_drain(self):
        if self.root and self.root.winfo_exists():
            try:
                while True:
                    msg = self.log_queue.get_nowait()
                    self.log_text.insert(tk.END, msg)
                self.log_text.see(tk.END)
            except queue.Empty:
                pass
            self.root.after(50, self._periodic_log_drain)

    def _snap_slot(self, val):
        snapped = round(float(val))
        self.current_slot.set(snapped)

    def add_osc(self):
        count = len([m for m in self.modules if m.startswith('osc_')])
        mod_id = f"osc_{count}"
        mod = OscModule(mod_id, self.root)
        self.modules[mod_id] = mod
        self.deferred_modules.append((mod_id, "OSC"))
        if self.viewer:
            self.viewer.add_module(mod_id, "OSC")
        self.log_text.insert(tk.END, f"Added OSC {mod_id}\n")
        self.log_text.see(tk.END)

    def add_lfo(self):
        count = len([m for m in self.modules if m.startswith('lfo_')])
        mod_id = f"lfo_{count}"
        mod = LfoModule(mod_id, self.root)
        self.modules[mod_id] = mod
        self.deferred_modules.append((mod_id, "LFO"))
        if self.viewer:
            self.viewer.add_module(mod_id, "LFO")
        self.log_text.insert(tk.END, f"Added LFO {mod_id}\n")
        self.log_text.see(tk.END)

    def add_audio_out(self):
        count = len([m for m in self.modules if m.startswith('audio_out_')])
        mod_id = f"audio_out_{count}"
        mod = AudioOutModule(mod_id, self.root)
        self.modules[mod_id] = mod
        self.deferred_modules.append((mod_id, "Audio Out"))
        if self.viewer:
            self.viewer.add_module(mod_id, "Audio Out")
        self.log_text.insert(tk.END, f"Added Audio Out {mod_id}\n")
        self.log_text.see(tk.END)

    def launch_test_rig(self):
        rig = tk.Toplevel(self.root)
        rig.title("Test Rig")
        rig.geometry("1200x800")
        paned = ttk.PanedWindow(rig, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        # OSC sub-frame
        osc_sub = ttk.Frame(paned)
        paned.add(osc_sub, weight=1)
        ttk.Label(osc_sub, text="OSC Modules").pack()
        osc_mod_frame = ttk.Frame(osc_sub)
        osc_mod_frame.pack(fill=tk.BOTH, expand=True)
        count = len([m for m in self.modules if m.startswith('osc_')])
        mod_id = f"osc_{count}"
        mod = OscModule(mod_id, osc_mod_frame)
        self.modules[mod_id] = mod
        self.deferred_modules.append((mod_id, "OSC"))
        self.log_text.insert(tk.END, f"Added OSC {mod_id} to rig\n")
        self.log_text.see(tk.END)

        # LFO sub-frame
        lfo_sub = ttk.Frame(paned)
        paned.add(lfo_sub, weight=1)
        ttk.Label(lfo_sub, text="LFO Modules").pack()
        lfo_mod_frame = ttk.Frame(lfo_sub)
        lfo_mod_frame.pack(fill=tk.BOTH, expand=True)
        count = len([m for m in self.modules if m.startswith('lfo_')])
        mod_id = f"lfo_{count}"
        mod = LfoModule(mod_id, lfo_mod_frame)
        self.modules[mod_id] = mod
        self.deferred_modules.append((mod_id, "LFO"))
        self.log_text.insert(tk.END, f"Added LFO {mod_id} to rig\n")
        self.log_text.see(tk.END)

        # Audio Out sub-frame
        audio_sub = ttk.Frame(paned)
        paned.add(audio_sub, weight=1)
        ttk.Label(audio_sub, text="Audio Out Modules").pack()
        audio_mod_frame = ttk.Frame(audio_sub)
        audio_mod_frame.pack(fill=tk.BOTH, expand=True)
        count = len([m for m in self.modules if m.startswith('audio_out_')])
        mod_id = f"audio_out_{count}"
        mod = AudioOutModule(mod_id, audio_mod_frame)
        self.modules[mod_id] = mod
        self.deferred_modules.append((mod_id, "Audio Out"))
        self.log_text.insert(tk.END, f"Added Audio Out {mod_id} to rig\n")
        self.log_text.see(tk.END)

    def inquiry_caps(self):
        msg = ProtocolMessage(ProtocolMessageType.CAPABILITIES_INQUIRY.value, "mcu")
        self.mcu_sock.sendto(msg.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
        self.log_text.insert(tk.END, "Broadcast CAPABILITIES_INQUIRY\n")
        self.log_text.see(tk.END)

    def inquiry_state(self):
        msg = ProtocolMessage(ProtocolMessageType.STATE_INQUIRY.value, "mcu")
        self.mcu_sock.sendto(msg.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
        self.log_text.insert(tk.END, "Broadcast STATE_INQUIRY\n")
        self.log_text.see(tk.END)

    def save_patch(self):
        self.inquiry_state()
        self.root.after(500, self._collect_and_save)

    def _collect_and_save(self):
        slot = self.current_slot.get()
        self.saved_patches[slot] = {mid: self.module_states.get(mid, {}) for mid in self.modules}
        count = len(self.saved_patches[slot])
        self.log_text.insert(tk.END, f"Saved patch slot {slot} with {count} modules\n")
        self.log_text.see(tk.END)

    def restore_patch(self):
        slot = self.current_slot.get()
        if slot not in self.saved_patches or not self.saved_patches[slot]:
            self.log_text.insert(tk.END, f"No saved patch in slot {slot}\n")
            self.log_text.see(tk.END)
            return
        restored_count = 0
        for mod_id, state in self.saved_patches[slot].items():
            payload = {'target_mod': mod_id, **state}
            msg = ProtocolMessage(ProtocolMessageType.PATCH_RESTORE.value, "mcu", payload=payload)
            self.mcu_sock.sendto(msg.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
            self.log_text.insert(tk.END, f"Sent PATCH_RESTORE for {mod_id}\n")
            restored_count += 1
        self.log_text.insert(tk.END, f"Multicast restored {restored_count} modules from slot {slot}\n")
        self.log_text.see(tk.END)
        self.root.after(200, self._update_graph)

    def show_graph(self):
        if self.viewer and self.viewer.winfo_exists():
            self.viewer.lift()
        else:
            self.viewer = PatchViewer(self.root)
            for mod_id, typ in self.deferred_modules:
                self.viewer.add_module(mod_id, typ)
            self.deferred_modules = []
        self._update_graph()

    def _update_graph(self):
        if not self.viewer:
            return
        for mod_id, mod in self.modules.items():
            self.viewer.update_params(mod_id, mod.controls)
        for mod_id, mod in self.modules.items():
            for io, info in mod.inputs.items():
                src = info.get("src")
                if src:
                    self.viewer.connect(src, f"{mod_id}:{io}")

    def on_closing(self):
        for mod in self.modules.values():
            mod.on_closing()
        self.mcu_sock.close()
        self.root.destroy()

if __name__ == "__main__":
    app = MainApp()
    try:
        app.root.mainloop()
    except KeyboardInterrupt:
        app.on_closing()

# main.py ends here