import tkinter as tk
from tkinter import ttk, Text, Scrollbar
import json
import threading
import time
import logging
import socket
import struct
from typing import Dict, List, Tuple
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
        self.deferred_modules: List[Tuple[str, str]] = []
        self.viewer: PatchViewer = None
        self.CV_GROUP = '239.100.1.1'
        self.sample_patch = {
            "controls": {"freq": 880.0, "fm_depth": 0.3, "rate": 2.0},
            "inputs": {"fm": {"src": "lfo_0:cv", "group": self.CV_GROUP}}
        }
        self._setup_gui()
        self._setup_mcu()
        # No polling - event-driven only
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
                data, _ = self.mcu_sock.recvfrom(1024)
                msg = ProtocolMessage.unpack(data)
                if msg.type in [ProtocolMessageType.CAPABILITIES_RESPONSE.value, ProtocolMessageType.STATE_RESPONSE.value]:
                    payload = json.dumps(msg.payload, indent=2)
                    self.log_text.insert(tk.END, f"Received {ProtocolMessageType(msg.type).name} from {msg.module_id}:\n{payload}\n\n")
                elif msg.type in [ProtocolMessageType.INITIATE.value, ProtocolMessageType.CONNECT.value, ProtocolMessageType.CANCEL.value]:
                    self.log_text.insert(tk.END, f"Received {ProtocolMessageType(msg.type).name} from {msg.module_id}:{msg.io_id} (group: {msg.payload.get('group', 'N/A')})\n")
                self.log_text.see(tk.END)
            except socket.timeout:
                pass
            except Exception as e:
                logger.warning(f"MCU listener error: {e}")

    def _setup_gui(self):
        ttk.Button(self.root, text="Add OSC", command=self.add_osc).pack(pady=5)
        ttk.Button(self.root, text="Add LFO", command=self.add_lfo).pack(pady=5)
        ttk.Button(self.root, text="Add Audio Out", command=self.add_audio_out).pack(pady=5)
        ttk.Button(self.root, text="Inquiry Caps", command=self.inquiry_caps).pack(pady=5)
        ttk.Button(self.root, text="Inquiry State", command=self.inquiry_state).pack(pady=5)
        ttk.Button(self.root, text="Restore Patch", command=self.restore_patch).pack(pady=5)
        ttk.Button(self.root, text="Show Graph", command=self.show_graph).pack(pady=5)
        scrollbar = Scrollbar(self.root)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text = Text(self.root, height=15, yscrollcommand=scrollbar.set, wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        scrollbar.config(command=self.log_text.yview)
        self.log_text.insert(tk.END, "DMS Simulator Ready. Add modules to start.\n")

    def add_osc(self):
        mod_id = f"osc_{len([m for m in self.modules if m.startswith('osc_')])}"
        mod = OscModule(mod_id, self.root)
        self.modules[mod_id] = mod
        self.deferred_modules.append((mod_id, "OSC"))
        if self.viewer:
            self.viewer.add_module(mod_id, "OSC")
        self.log_text.insert(tk.END, f"Added OSC {mod_id}\n")
        self.log_text.see(tk.END)

    def add_lfo(self):
        mod_id = f"lfo_{len([m for m in self.modules if m.startswith('lfo_')])}"
        mod = LfoModule(mod_id, self.root)
        self.modules[mod_id] = mod
        self.deferred_modules.append((mod_id, "LFO"))
        if self.viewer:
            self.viewer.add_module(mod_id, "LFO")
        self.log_text.insert(tk.END, f"Added LFO {mod_id}\n")
        self.log_text.see(tk.END)

    def add_audio_out(self):
        mod_id = f"audio_out_{len([m for m in self.modules if m.startswith('audio_out_')])}"
        mod = AudioOutModule(mod_id, self.root)
        self.modules[mod_id] = mod
        self.deferred_modules.append((mod_id, "Audio Out"))
        if self.viewer:
            self.viewer.add_module(mod_id, "Audio Out")
        self.log_text.insert(tk.END, f"Added Audio Out {mod_id}\n")
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

    def restore_patch(self):
        if 'lfo_0' in self.modules:
            self.sample_patch["inputs"]["fm"]["src"] = "lfo_0:cv"
        msg = ProtocolMessage(ProtocolMessageType.PATCH_RESTORE.value, "mcu", payload=self.sample_patch)
        self.mcu_sock.sendto(msg.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
        self.log_text.insert(tk.END, f"Sent PATCH_RESTORE (src: {self.sample_patch['inputs']['fm']['src']})\n")
        self.log_text.see(tk.END)
        # Explicit sync (no poll)
        self.root.after(100, lambda: [mod._sync_ui() for mod in self.modules.values()])
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