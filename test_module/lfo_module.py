# lfo_module.py — FINAL WORKING VERSION
import tkinter as tk
from tkinter import ttk
import threading
import time
import math
import struct
import socket
import logging

from base_module import BaseModule, LedState, ProtocolMessage, ProtocolMessageType, CONTROL_MULTICAST, UDP_CONTROL_PORT, JackWidget
from connection_protocol import ConnectionProtocol, InputState, OutputState  
from patch_protocol import PatchProtocol

logger = logging.getLogger(__name__)

UDP_CV_PORT = 5005
LFO_RATE = 1000
HYSTERESIS = 0.01

class LfoModule(ConnectionProtocol, PatchProtocol, BaseModule):
    def __init__(self, mod_id: str, parent_root: tk.Tk = None):
        # 1. BaseModule first — creates socket, mcast_group, etc.
        super().__init__(mod_id, "lfo")

        # 2. Define I/O and controls — AFTER super()
        self.inputs = {}
        self.outputs = {"cv": {"type": "cv", "group": self.mcast_group}}

        self.control_ranges = {"rate": [0.01, 30.0]}
        self.controls = {"rate": 1.0}

        self.phase = 0.0
        self._cv_thread = None
        self.control_vars = {}
        self.rate_var = tk.DoubleVar(value=1.0)
        self.control_vars["rate"] = self.rate_var
        

        # 3. Initialize connection states (must come after inputs/outputs defined)
        # self._init_connection_states()
        # self._sync_initial_leds()
        self._ensure_io_defs()          # ← NEW
        self._refresh_gui_from_controls()   # ← now comes from ConnectionProtocol

        # 4. Build GUI
        self._setup_gui(parent_root)
        self.set_root(self.root)

        # 5. Final refresh (protects pending states + restores slider on patch load)
        if hasattr(self, "_refresh_gui_from_controls"):
            self._refresh_gui_from_controls()

        # 6. Start sending CV
        self.start_sending()

    def _setup_gui(self, parent_root):
        self.root = tk.Toplevel(parent_root) if parent_root else tk.Tk()
        self.root.title(f"LFO {self.module_id}")

        self.gui_leds["cv"] = JackWidget(
            self.root, "cv", "CV Out",
            short_press_callback=self.initiate_connect,
            verbose_text=False
        )
        self.gui_leds["cv"].pack(pady=10)

        ttk.Label(self.root, text="Rate (Hz)").pack()
        self.rate_scale = ttk.Scale(
            self.root,
            from_=0.01,
            to=30.0,
            orient="horizontal",
            variable=self.rate_var,
            command=self._on_rate_change
        )
        self.rate_scale.pack(pady=5, fill="x", padx=20)

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def _on_rate_change(self, val):
        try:
            f = float(val)
            if abs(f - self.controls["rate"]) >= HYSTERESIS:
                self.controls["rate"] = f
        except ValueError:
            pass


    def start_sending(self):
        if self._cv_thread is None or not self._cv_thread.is_alive():
            self._cv_thread = threading.Thread(target=self._cv_loop, daemon=True)
            self._cv_thread.start()

    def _cv_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        ttl = struct.pack('b', 1)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, ttl)
        dest = (self.outputs["cv"]["group"], UDP_CV_PORT)

        while True:
            rate = self.controls["rate"]
            inc = 2 * math.pi * rate / LFO_RATE
            self.phase += inc
            self.phase %= 2 * math.pi
            cv = 0.5 * (1.0 + math.sin(self.phase))   # 0..1 range
            data = struct.pack('<f', cv)

            try:
                sock.sendto(data, dest)
            except Exception as e:
                logger.warning(f"[{self.module_id}] CV send error: {e}")

            time.sleep(1.0 / LFO_RATE)

    def on_closing(self):
        super().on_closing()
        if self.root:
            self.root.destroy()

    def handle_msg(self, msg: ProtocolMessage):
        super().handle_msg(msg)