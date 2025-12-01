# main.py — FINAL (no socket, modules own theirs)
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import json
import threading
import socket
import struct
import logging

from osc_module import OscModule
from lfo_module import LfoModule
from audio_out_module import AudioOutModule
from module import ProtocolMessage, ProtocolMessageType, CONTROL_MULTICAST, UDP_CONTROL_PORT

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class MainApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("DMS Control Panel")
        self.root.geometry("520x800")

        self.modules = {}
        self.saved_states = []
        self.patch_memory = [[] for _ in range(5)]
        self.patch_slot_var = tk.IntVar(value=0)

        self._setup_gui()
        self._setup_listener()

    def _setup_gui(self):
        frame = ttk.Frame(self.root, padding="10")
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Button(frame, text="Add Oscillator", command=self.add_osc).pack(fill=tk.X, pady=4)
        ttk.Button(frame, text="Add LFO", command=self.add_lfo).pack(fill=tk.X, pady=4)
        ttk.Button(frame, text="Add Audio Out", command=self.add_audio_out).pack(fill=tk.X, pady=4)

        ttk.Separator(frame, orient='horizontal').pack(fill=tk.X, pady=12)

        ttk.Button(frame, text="Discover Modules (Caps Inquiry)", command=self.discover_modules).pack(fill=tk.X, pady=4)

        ttk.Label(frame, text="Patch Slot (0-4)").pack(pady=4)
        ttk.Scale(frame, from_=0, to=4, orient="horizontal", variable=self.patch_slot_var).pack(fill=tk.X, pady=4)
        ttk.Button(frame, text="Save to Slot", command=self.save_to_slot).pack(fill=tk.X, pady=4)
        ttk.Button(frame, text="Load from Slot", command=self.load_from_slot).pack(fill=tk.X, pady=4)

        ttk.Button(frame, text="Export Patch to File", command=self.save_patch).pack(fill=tk.X, pady=4)
        ttk.Button(frame, text="Import Patch from File", command=self.restore_patch).pack(fill=tk.X, pady=4)

        ttk.Separator(frame, orient='horizontal').pack(fill=tk.X, pady=12)

        log_frame = ttk.LabelFrame(frame, text="Log")
        log_frame.pack(fill=tk.BOTH, expand=True, pady=8)

        self.log_text = tk.Text(log_frame, height=20, state='disabled', wrap='word')
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def _setup_listener(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, 'SO_REUSEPORT'):
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        self.sock.bind(('', UDP_CONTROL_PORT))

        mreq = struct.pack("4sl", socket.inet_aton(CONTROL_MULTICAST), socket.INADDR_ANY)
        self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

        self.sock.settimeout(0.1)  # Non-blocking recv

        self.listener_thread = threading.Thread(target=self._listener_loop, daemon=True)
        self.listener_thread.start()

    def _listener_loop(self):
        while True:
            try:
                data, _ = self.sock.recvfrom(1024)
                msg = ProtocolMessage.unpack(data)
                if msg.type == ProtocolMessageType.STATE_RESPONSE.value:
                    state_with_id = {**msg.payload, "module_id": msg.module_id}
                    self.saved_states.append(state_with_id)
                    self._log(f"Received state from {msg.module_id}")
                elif msg.type == ProtocolMessageType.CAPABILITIES_RESPONSE.value:
                    self._log(f"Received capabilities from {msg.module_id}: {json.dumps(msg.payload, indent=2)}")
            except socket.timeout:
                continue
            except Exception as e:
                logger.warning(f"Listener error: {e}")

    def _log(self, text: str):
        self.log_text.config(state='normal')
        self.log_text.insert(tk.END, f"{text}\n")
        self.log_text.see(tk.END)
        self.log_text.config(state='disabled')

    def discover_modules(self):
        msg = ProtocolMessage(ProtocolMessageType.CAPABILITIES_INQUIRY.value, "mcu")
        self.sock.sendto(msg.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
        self._log("Sent CAPABILITIES_INQUIRY to discover modules")

    def add_osc(self):
        n = len([m for m in self.modules.values() if m.type == "osc"])
        mod = OscModule(f"osc_{n}", self.root)
        self.modules[mod.module_id] = mod
        self._log(f"Added {mod.module_id} @ {mod.unicast_ip} → {mod.mcast_group}")

    def add_lfo(self):
        n = len([m for m in self.modules.values() if m.type == "lfo"])
        mod = LfoModule(f"lfo_{n}", self.root)
        self.modules[mod.module_id] = mod
        self._log(f"Added {mod.module_id} @ {mod.unicast_ip} → {mod.mcast_group}")

    def add_audio_out(self):
        n = len([m for m in self.modules.values() if m.type == "audio_out"])
        mod = AudioOutModule(f"audio_out_{n}", self.root)
        self.modules[mod.module_id] = mod
        self._log(f"Added {mod.module_id} @ {mod.unicast_ip} → {mod.mcast_group}")

    def save_to_slot(self):
        self.saved_states.clear()
        self._log("Starting save to slot: Discovering modules...")
        cap_msg = ProtocolMessage(ProtocolMessageType.CAPABILITIES_INQUIRY.value, "mcu")
        self.sock.sendto(cap_msg.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
        self.root.after(1000, self._send_state_inquiry_for_slot)

    def _send_state_inquiry_for_slot(self):
        self._log("Requesting state from all modules...")
        state_msg = ProtocolMessage(ProtocolMessageType.STATE_INQUIRY.value, "mcu")
        self.sock.sendto(state_msg.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
        self.root.after(1000, self._store_to_slot)

    def _store_to_slot(self):
        if not self.saved_states:
            messagebox.showwarning("Warning", "No module states received. Ensure modules are running and responsive.")
            return
        slot = self.patch_slot_var.get()
        self.patch_memory[slot] = self.saved_states[:]
        self._log(f"Saved to slot {slot} ({len(self.saved_states)} modules)")

    def load_from_slot(self):
        slot = self.patch_slot_var.get()
        states = self.patch_memory[slot]
        if not states:
            self._log(f"No patch in slot {slot}")
            return
        for state in states:
            mod_id = state.get("module_id")
            if mod_id:
                payload = {
                    "target_mod": mod_id,
                    "controls": state.get("controls", {}),
                    "connections": state.get("connections", {})
                }
                msg = ProtocolMessage(ProtocolMessageType.PATCH_RESTORE.value, "mcu", payload=payload)
                self.sock.sendto(msg.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
                if mod_id in self.modules:
                    self._log(f"Restored → {mod_id} from slot {slot}")
                else:
                    self._log(f"Skipped restore for missing module {mod_id}")
        self._log("Load from slot complete")
        self.root.after(500, self._refresh_all_modules)

    def save_patch(self):
        self.collected_states = {}
        msg = ProtocolMessage(ProtocolMessageType.STATE_INQUIRY.value, "mcu")
        self.mcu_sock.sendto(msg.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
        self.log_text.insert(tk.END, "Broadcast STATE_INQUIRY\n")
        self.log_text.see(tk.END)

        # Collect responses with timeout
        def collect_responses():
            self.root.after(500, self._check_states_collected)  # 500ms timeout

        self.root.after(100, collect_responses)

    def _check_states_collected(self):
        if len(self.collected_states) == 0:
            # Popup error
            from tkinter import messagebox
            messagebox.showerror("Save Failed", "No module states received. Ensure modules are running and responsive.")
            return
        # Save to file or whatever — for now, log
        self.log_text.insert(tk.END, f"Saved {len(self.collected_states)} states\n")
        self.log_text.see(tk.END)

    def _send_state_inquiry_for_file(self):
        self._log("Requesting state from all modules...")
        state_msg = ProtocolMessage(ProtocolMessageType.STATE_INQUIRY.value, "mcu")
        self.sock.sendto(state_msg.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
        self.root.after(1000, self._prompt_save_file)

    def _prompt_save_file(self):
        if not self.saved_states:
            messagebox.showwarning("Warning", "No module states received. Ensure modules are running and responsive.")
            return
        filename = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")])
        if not filename:
            self._log("Export cancelled")
            return
        try:
            with open(filename, "w") as f:
                json.dump(self.saved_states, f, indent=2)
            self._log(f"Exported patch to {filename} ({len(self.saved_states)} modules)")
        except Exception as e:
            messagebox.showerror("Error", f"Cannot save: {e}")

    def restore_patch(self):
        filename = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
        if not filename:
            self._log("Import cancelled")
            return
        try:
            with open(filename, "r") as f:
                states = json.load(f)
        except Exception as e:
            messagebox.showerror("Error", f"Cannot load: {e}")
            return

        for state in states:
            mod_id = state.get("module_id")
            if mod_id:
                payload = {
                    "target_mod": mod_id,
                    "controls": state.get("controls", {}),
                    "connections": state.get("connections", {})
                }
                msg = ProtocolMessage(ProtocolMessageType.PATCH_RESTORE.value, "mcu", payload=payload)
                self.sock.sendto(msg.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
                if mod_id in self.modules:
                    self._log(f"Restored → {mod_id}")
                else:
                    self._log(f"Skipped restore for missing module {mod_id}")
        self._log("Patch import complete")
        self.root.after(500, self._refresh_all_modules)

    def _refresh_all_modules(self):
        for mod in self.modules.values():
            if hasattr(mod, '_refresh_all_widgets'):
                mod._refresh_all_widgets()
        self._log("Refreshed all module GUIs")
        
    def _mcu_listener(self):
        while True:
            try:
                data, _ = self.mcu_sock.recvfrom(1024)
                msg = ProtocolMessage.unpack(data)
                if msg.type == ProtocolMessageType.STATE_RESPONSE.value:
                    mod_id = msg.module_id
                    state = msg.payload  # Assume modules send get_state() as payload
                    self.collected_states[mod_id] = state
                    self.log_text.insert(tk.END, f"Collected state from {mod_id}\n")
                elif msg.type == ProtocolMessageType.CAPABILITIES_RESPONSE.value:
                    # Your existing log
                    payload = json.dumps(msg.payload, indent=2)
                    self.log_text.insert(tk.END, f"Received {ProtocolMessageType(msg.type).name} from {msg.module_id}:\n{payload}\n\n")
                # ... other logs
                self.log_text.see(tk.END)
            except socket.timeout:
                pass
            except Exception as e:
                logger.warning(f"MCU listener error: {e}")

    def on_closing(self):
        if messagebox.askokcancel("Quit", "Close DMS Control Panel?"):
            try:
                self.sock.close()
            except:
                pass
            self.root.destroy()

if __name__ == "__main__":
    app = MainApp()
    app.root.mainloop()