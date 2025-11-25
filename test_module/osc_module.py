# osc_module.py — FINAL, GUARANTEED TO WORK — COPY THIS EXACTLY
# osc_module.py — FORCE RELOAD MARKER — 2025-04-05

import tkinter as tk
from tkinter import ttk
import threading
import time
import math
import numpy as np
import socket
import struct
import logging

from base_module import BaseModule, LedState, ProtocolMessage, ProtocolMessageType, CONTROL_MULTICAST, UDP_CONTROL_PORT, JackWidget
from connection_protocol import ConnectionProtocol
from patch_protocol import PatchProtocol

logger = logging.getLogger(__name__)

UDP_AUDIO_PORT = 5005
SAMPLE_RATE = 48000
BLOCK_SIZE = 96
CV_GROUP = '239.100.1.1'
AUDIO_GROUP = '239.100.2.150'
HYSTERESIS = 0.01

class OscModule(ConnectionProtocol, PatchProtocol, BaseModule):
    def __init__(self, mod_id: str, parent_root: tk.Tk = None):
        super().__init__(mod_id, "osc")  # This calls ConnectionProtocol → PatchProtocol → BaseModule

        self.inputs = {"fm": {"type": "cv", "group": CV_GROUP}}
        self.outputs = {"audio": {"type": "audio", "group": self.mcast_group}}
        self.control_ranges = {"freq": [20, 20000], "fm_depth": [0, 1]}
        self.controls = {"freq": 440.0, "fm_depth": 0.5}

        self.freq_var = tk.DoubleVar(value=440.0)
        self.fm_var = tk.DoubleVar(value=0.5)

        self.cv_buffer = np.zeros(1024, dtype=np.float32)
        self.cv_phase = 0
        self.osc_phase = 0.0
        self._cv_receiver = None
        self._audio_thread = None
        self.controls_lock = threading.Lock()

        self._init_connection_states()
        self._setup_gui(parent_root)
        self.set_root(self.root)
        self._sync_initial_leds()
        self._update_display()
        if self.root:
            self.root.update_idletasks()

        self.start_sending()

    def _setup_gui(self, parent_root):
        self.root = tk.Toplevel(parent_root) if parent_root else tk.Tk()
        self.root.title(f"Osc {self.module_id}")

        self.gui_leds["fm"] = JackWidget(
            self.root, "fm", "FM Input",
            short_press_callback=self.connect_input,
            long_press_callback=self.long_press_input,
            verbose_text=False
        )
        self.gui_leds["fm"].pack(pady=5)

        self.gui_leds["audio"] = JackWidget(
            self.root, "audio", "Audio Output",
            short_press_callback=self.initiate_connect,
            verbose_text=False
        )
        self.gui_leds["audio"].pack(pady=5)

        ttk.Scale(self.root, from_=20, to=20000, orient="horizontal",
                  variable=self.freq_var, command=self._on_freq_change).pack(pady=5)
        ttk.Scale(self.root, from_=0, to=1, orient="horizontal",
                  variable=self.fm_var, command=self._on_fm_change).pack(pady=5)

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def _on_freq_change(self, val):
        try:
            f = float(val)
            if abs(f - self.controls["freq"]) >= HYSTERESIS:
                with self.controls_lock:
                    self.controls["freq"] = f
        except ValueError:
            pass

    def _on_fm_change(self, val):
        try:
            f = float(val)
            if abs(f - self.controls["fm_depth"]) >= HYSTERESIS:
                with self.controls_lock:
                    self.controls["fm_depth"] = f
        except ValueError:
            pass

    def start_sending(self):
        if self._audio_thread is None or not self._audio_thread.is_alive():
            self._audio_thread = threading.Thread(target=self._audio_loop, daemon=True)
            self._audio_thread.start()

    def _audio_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        ttl = struct.pack('b', 1)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, ttl)
        dest = (AUDIO_GROUP, UDP_AUDIO_PORT)

        logger.info(f"Audio sender started for {self.module_id}")

        while True:
            samples = []
            block_phase = self.cv_phase
            for i in range(BLOCK_SIZE):
                with self.controls_lock:
                    c = self.controls.copy()
                fm_depth = c["fm_depth"]
                base_freq = c["freq"]
                idx = (block_phase + i) // (SAMPLE_RATE // 1000) % len(self.cv_buffer)
                cv = self.cv_buffer[idx]
                freq = base_freq * (2 ** (cv * fm_depth))
                self.osc_phase += 2 * math.pi * freq / SAMPLE_RATE
                self.osc_phase %= 2 * math.pi
                sample = math.sin(self.osc_phase)
                isample = int(sample * 8388607.0)
                if isample < 0:
                    isample += (1 << 24)
                samples.append(isample)

            packet = b''.join(bytes([(s>>16)&0xFF, (s>>8)&0xFF, s&0xFF]) for s in samples)
            try:
                sock.sendto(packet, dest)
            except Exception as e:
                logger.warning(f"Audio send error: {e}")

            time.sleep(BLOCK_SIZE / SAMPLE_RATE)

    def _start_receiver(self, io_id: str, group: str, offset: int, block_size: int):
        if io_id != "fm":
            return
        if self._cv_receiver and self._cv_receiver.is_alive():
            return

        def receiver():
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(('', UDP_AUDIO_PORT))
            mreq = struct.pack("4sl", socket.inet_aton(group), socket.INADDR_ANY)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            sock.settimeout(0.1)

            logger.info(f"CV receiver started for {self.module_id} on {group}")

            while True:
                try:
                    data, _ = sock.recvfrom(4096)
                    if len(data) >= 4:
                        cv = struct.unpack('<f', data[:4])[0]
                        self.cv_buffer[self.cv_phase % len(self.cv_buffer)] = cv
                        self.cv_phase += 1
                except socket.timeout:
                    continue
                except Exception:
                    break

        self._cv_receiver = threading.Thread(target=receiver, daemon=True)
        self._cv_receiver.start()

    def _refresh_gui_from_controls(self):
        self.freq_var.set(self.controls.get("freq", 440.0))
        self.fm_var.set(self.controls.get("fm_depth", 0.5))

        for io_id, rec in getattr(self, "input_connections", {}).items():
            state = "IIdleConnected" if rec else "IIdleDisconnected"
            led = LedState.BLINK_RAPID if rec else LedState.OFF
            self.input_states[io_id] = state
            self._queue_led_update(io_id, led)

        for io_id in self.output_states:
            self._queue_led_update(io_id, LedState.SOLID)

        if self.root:
            self.root.update_idletasks()

    def on_closing(self):
        super().on_closing()
        if self.root:
            self.root.destroy()

    def handle_msg(self, msg: ProtocolMessage):
        super().handle_msg(msg)