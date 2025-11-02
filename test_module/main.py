# main.py
import tkinter as tk
from tkinter import ttk, messagebox, Text, Scrollbar
import json
import threading
import time
import logging
import socket
import struct
from typing import Dict, List
from osc_module import OscModule
from lfo_module import LfoModule
from audio_out_module import AudioOutModule
from graph_viewer import PatchViewer
from base_module import ProtocolMessage, ProtocolMessageType, CONTROL_MULTICAST, UDP_CONTROL_PORT

logger = logging.getLogger(__name__)

class MainApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("DMS Simulator")
        self.root.geometry("400x300")
        self.modules: Dict[str, OscModule | LfoModule | AudioOutModule] = {}
        self.deferred_modules: List[Tuple[str, str]] = []
        self.viewer = None
        self.sample_patch = {
            "controls": {"freq": 880, "fm_depth": 0.3},
            "inputs": {"fm": {"src": "lfo_1:cv", "group": "239.100.3.123"}}
        }
        self._setup_gui()
        self._setup_mcu()
        self.root.after(50, self._periodic_update)
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def _periodic_update(self):
        for mod in self.modules.values():
            mod._update_display()
        self.root.after(50, self._periodic_update)

    def _setup_mcu(self):
        self.mcu_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.mcu_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.mcu_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        self.mcu_sock.bind(('', UDP_CONTROL_PORT))
        mreq = struct.pack("4sl", socket.inet_aton(CONTROL_MULTICAST), socket.INADDR_ANY)
        self.mcu_sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        self.mcu_sock.settimeout(0.001)
        self._mcu_listener_thread = threading.Thread(target=self._mcu_listener, daemon=True)
        self._mcu_listener_thread.start()

    def _mcu_listener(self):
        while True:
            try:
                data, _ = self.mcu_sock.recvfrom(1024)
                msg = ProtocolMessage.unpack(data)
                if msg.type in [ProtocolMessageType.CAPABILITIES_RESPONSE.value, ProtocolMessageType.STATE_RESPONSE.value]:
                    payload = json.dumps(msg.payload, indent=2)
                    self.log_text.insert(tk.END, f"Received {ProtocolMessageType(msg.type).name}: {payload}\n")
                    self.log_text.see(tk.END)
                elif msg.type in [ProtocolMessageType.INITIATE.value, ProtocolMessageType.CONNECT.value, ProtocolMessageType.CANCEL.value]:
                    self.log_text.insert(tk.END, f"Received {ProtocolMessageType(msg.type).name} from {msg.module_id}:{msg.io_id}\n")
                    self.log_text.see(tk.END)
            except socket.timeout:
                pass
            except Exception as e:
                logger.warning(f"MCU listener error: {e}")

    def _setup_gui(self):
        add_osc_btn = ttk.Button(self.root, text="Add OSC", command=self.add_osc)
        add_osc_btn.pack()
        add_lfo_btn = ttk.Button(self.root, text="Add LFO", command=self.add_lfo)
        add_lfo_btn.pack()
        add_audio_out_btn = ttk.Button(self.root, text="Add Audio Out", command=self.add_audio_out)
        add_audio_out_btn.pack()
        inquiry_caps_btn = ttk.Button(self.root, text="Inquiry Caps", command=self.inquiry_caps)
        inquiry_caps_btn.pack()
        inquiry_state_btn = ttk.Button(self.root, text="Inquiry State", command=self.inquiry_state)
        inquiry_state_btn.pack()
        restore_btn = ttk.Button(self.root, text="Restore Patch", command=self.restore_patch)
        restore_btn.pack()
        show_graph_btn = ttk.Button(self.root, text="Show Graph", command=self.show_graph)
        show_graph_btn.pack()
        scrollbar = Scrollbar(self.root)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text = Text(self.root, height=10, yscrollcommand=scrollbar.set)
        self.log_text.pack(fill=tk.BOTH)
        scrollbar.config(command=self.log_text.yview)

    def add_osc(self):
        mod_id = f"osc_{len([m for m in self.modules if 'osc' in m])}"
        mod = OscModule(mod_id, self.root)
        self.modules[mod_id] = mod
        self.deferred_modules.append((mod_id, "OSC"))
        if self.viewer:
            self.viewer.add_module(mod_id, "OSC")
        self.log_text.insert(tk.END, f"Added OSC {mod_id}\n")
        self.log_text.see(tk.END)

    def add_lfo(self):
        mod_id = f"lfo_{len([m for m in self.modules if 'lfo' in m])}"
        mod = LfoModule(mod_id, self.root)
        self.modules[mod_id] = mod
        self.deferred_modules.append((mod_id, "LFO"))
        if self.viewer:
            self.viewer.add_module(mod_id, "LFO")
        self.log_text.insert(tk.END, f"Added LFO {mod_id}\n")
        self.log_text.see(tk.END)

    def add_audio_out(self):
        mod_id = f"audio_out_{len([m for m in self.modules if 'audio_out' in m])}"
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
        self.log_text.insert(tk.END, "Sent CAPABILITIES_INQUIRY\n")
        self.log_text.see(tk.END)

    def inquiry_state(self):
        msg = ProtocolMessage(ProtocolMessageType.STATE_INQUIRY.value, "mcu")
        self.mcu_sock.sendto(msg.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
        self.log_text.insert(tk.END, "Sent STATE_INQUIRY\n")
        self.log_text.see(tk.END)

    def restore_patch(self):
        msg = ProtocolMessage(ProtocolMessageType.PATCH_RESTORE.value, "mcu", payload=self.sample_patch)
        self.mcu_sock.sendto(msg.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
        self.log_text.insert(tk.END, "Sent PATCH_RESTORE\n")
        self.log_text.see(tk.END)
        self.root.after(500, self._update_graph)

    def show_graph(self):
        if not self.viewer:
            self.viewer = PatchViewer(self.root)
            for mod_id, typ in self.deferred_modules:
                self.viewer.add_module(mod_id, typ)
            self.deferred_modules = []
        self.viewer.lift()
        self._update_graph()

    def _update_graph(self):
        if not self.viewer:
            return
        for mod_id, mod in self.modules.items():
            self.viewer.update_params(mod_id, mod.controls)
            for io, info in mod.inputs.items():
                src = info.get("src")
                if src:
                    dst = f"{mod_id}:{io}"
                    self.viewer.connect(src, dst)

    def on_closing(self):
        for mod in self.modules.values():
            mod.on_closing()
        self.mcu_sock.close()
        self.root.destroy()

if __name__ == "__main__":
    app = MainApp()
    app.root.mainloop()