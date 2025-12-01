# lfo_module.py — FINAL, WORKING VERSION

import tkinter as tk
from tkinter import ttk
import threading
import time
import math
import struct
import socket
import logging

from module import Module, KnobSlider, JackWidget
from connection_protocol import InputJack, OutputJack
logger = logging.getLogger(__name__)

UDP_CV_PORT = 5005
LFO_RATE = 1000

class LfoModule(Module):
    def __init__(self, mod_id: str, parent_root: tk.Tk = None):
        # Use loopback for simulator

        super().__init__(mod_id, "lfo")

        self.inputs = {}
        self.outputs = {"cv": {"type": "cv", "group": self.mcast_group}}
        self._init_jacks()

        self.phase = 0.0
        self.rate_var = tk.DoubleVar(value=1.0)

        # State machine
        self.output_jacks["cv"] = OutputJack("cv", self)

        # Force correct initial LED
        self.output_jacks["cv"]._set_led()

        # Knob
        self.knob_sliders["rate"] = KnobSlider("rate", (0.01, 30.0), self.rate_var)

        self._setup_gui(parent_root)
        self.set_root(self.root)

        self.start_sending()

    def _setup_gui(self, parent_root):
        self.root = tk.Toplevel(parent_root) if parent_root else tk.Tk()
        self.root.title(f"LFO {self.module_id} → {self.mcast_group}")

        self.gui_leds["cv"] = JackWidget(
            self.root, "cv", "CV Out",
            short_press_callback=self.output_jacks["cv"].short_press,
            long_press_callback=self.output_jacks["cv"].long_press,
            verbose_text=True,
            is_output=True,
        )
        self.gui_leds["cv"].pack(pady=10)

        ttk.Label(self.root, text="Rate (Hz)").pack()
        ttk.Scale(self.root, from_=0.01, to=30.0, variable=self.rate_var).pack(fill="x", padx=20)

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def start_sending(self):
        if hasattr(self, "_sender_thread") and self._sender_thread.is_alive():
            return
        self._sender_thread = threading.Thread(target=self._send_loop, daemon=True)
        self._sender_thread.start()

    def _send_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        ttl = struct.pack('b', 1)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, ttl)
        dest = (self.mcast_group, UDP_CV_PORT)

        while True:
            rate = self.rate_var.get()
            inc = 2 * math.pi * rate / LFO_RATE
            self.phase += inc
            self.phase %= 2 * math.pi
            cv = 0.5 * (1.0 + math.sin(self.phase))  # 0..1

            data = struct.pack('<f', cv)
            try:
                sock.sendto(data, dest)
            except:
                pass
            time.sleep(1.0 / LFO_RATE)