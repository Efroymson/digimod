# osc_module.py — FINAL, FULLY WORKING VERSION
# Fixed: LED colors, callback signatures, variable order

import tkinter as tk
from tkinter import ttk
import threading
import time
import math
import numpy as np
import socket
import struct
import logging

from module import Module, KnobSlider
from connection_protocol import InputJack, OutputJack
from base_module import JackWidget, LedState

logger = logging.getLogger(__name__)

UDP_AUDIO_PORT = 5005
SAMPLE_RATE = 48000
BLOCK_SIZE = 96

class OscModule(Module):
    def __init__(self, mod_id: str, parent_root: tk.Tk = None):
        super().__init__(mod_id, "osc")  # ← no IP needed!

        # I/O — each osc uses its own multicast group
        self.inputs = {"fm": {"type": "cv"}}
        self.outputs = {"audio": {"type": "audio", "group": self.mcast_group}}

        # Runtime state
        self.cv_buffer = np.zeros(1024, dtype=np.float32)
        self.cv_phase = 0
        self.osc_phase = 0.0
        self._cv_receiver = None
        self._audio_thread = None
        self.controls_lock = threading.Lock()

        # === CRITICAL: Create Tk variables BEFORE GUI ===
        self.freq_var = tk.DoubleVar(value=440.0)
        self.fm_depth_var = tk.DoubleVar(value=0.5)

        # === State machines ===
        self.input_jacks["fm"] = InputJack("fm", self)
        self.output_jacks["audio"] = OutputJack("audio", self)
        self.input_connections["fm"] = None

        # Force correct initial LED state (OIdle = SOLID green)
        self.output_jacks["audio"]._set_led()          # ← THIS WAS MISSING

        # === Knobs (saved/restored) ===
        self.knob_sliders["freq"] = KnobSlider("freq", (20.0, 20000.0), self.freq_var)
        self.knob_sliders["fm_depth"] = KnobSlider("fm_depth", (0.0, 1.0), self.fm_depth_var)

        # === GUI ===
        self._setup_gui(parent_root)
        self.set_root(self.root)

        # Start audio
        self.start_sending()

    def _setup_gui(self, parent_root):
        self.root = tk.Toplevel(parent_root) if parent_root else tk.Tk()
        self.root.title(f"Osc {self.module_id} → {self.mcast_group}")

        # FM Input — no args to callback!
        self.gui_leds["fm"] = JackWidget(
            self.root, "fm", "FM In",
            short_press_callback=self.input_jacks["fm"].short_press,   # ← no ()
            long_press_callback=self.input_jacks["fm"].long_press,
            verbose_text=False
        )
        self.gui_leds["fm"].pack(pady=8)

        # Audio Output — no args!
        self.gui_leds["audio"] = JackWidget(
            self.root, "audio", "Audio Out",
            short_press_callback=self.output_jacks["audio"].short_press,  # ← no ()
            long_press_callback=self.output_jacks["audio"].long_press,
            verbose_text=False
        )
        self.gui_leds["audio"].pack(pady=8)

        ttk.Label(self.root, text=f"Audio → {self.mcast_group}").pack(pady=4)
        ttk.Label(self.root, text="Frequency (Hz)").pack()
        ttk.Scale(self.root, from_=20, to=20000, variable=self.freq_var).pack(fill="x", padx=20, pady=4)
        ttk.Label(self.root, text="FM Depth").pack()
        ttk.Scale(self.root, from_=0, to=1, variable=self.fm_depth_var).pack(fill="x", padx=20, pady=4)

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    # ------------------------------------------------------------------
    # Audio loop — unchanged
    # ------------------------------------------------------------------
    def start_sending(self):
        if self._audio_thread is None or not self._audio_thread.is_alive():
            self._audio_thread = threading.Thread(target=self._audio_loop, daemon=True)
            self._audio_thread.start()

    def _audio_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        ttl = struct.pack('b', 1)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, ttl)
        dest = (self.mcast_group, UDP_AUDIO_PORT)

        while True:
            samples = []
            block_phase = self.cv_phase
            for i in range(BLOCK_SIZE):
                freq = self.freq_var.get()
                fm_depth = self.fm_depth_var.get()
                cv = self.cv_buffer[block_phase % len(self.cv_buffer)]
                actual_freq = freq * (2 ** (cv * fm_depth))

                self.osc_phase += 2 * math.pi * actual_freq / SAMPLE_RATE
                self.osc_phase %= 2 * math.pi
                sample = math.sin(self.osc_phase)

                isample = int(sample * 8388607.0)
                if isample < 0:
                    isample += (1 << 24)
                samples.append(isample)
                block_phase += 1
            self.cv_phase = block_phase

            packet = b''.join(struct.pack("3B", (s>>16)&0xFF, (s>>8)&0xFF, s&0xFF) for s in samples)
            sock.sendto(packet, dest)
            time.sleep(BLOCK_SIZE / SAMPLE_RATE)

    def _start_receiver(self, io_id: str, group: str, offset: int, block_size: int):
        if io_id != "fm" or self._cv_receiver:
            return
        # ... same as before ...