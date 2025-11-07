import tkinter as tk
from tkinter import ttk
import threading
import time
import math
import numpy as np
import socket
import struct
import logging
from typing import Dict
from base_module import BaseModule, LedState, ProtocolMessage, ProtocolMessageType, CONTROL_MULTICAST, UDP_CONTROL_PORT, RECV_TIMEOUT
from connection_protocol import ConnectionProtocol
from patch_protocol import PatchProtocol

logger = logging.getLogger(__name__)

UDP_AUDIO_PORT = 5005
SAMPLE_RATE = 48000
BLOCK_SIZE = 96
PACKET_SIZE = BLOCK_SIZE * 3
CV_GROUP = '239.100.1.1'
AUDIO_GROUP = '239.100.2.150'
HYSTERESIS = 0.01  # Skip small changes (sim ADC noise)

class OscModule(PatchProtocol, ConnectionProtocol, BaseModule):
    def __init__(self, mod_id: str, parent_root: tk.Tk = None):
        super().__init__(mod_id, "osc")
        self.control_ranges = {"freq": [20, 20000], "fm_depth": [0, 1]}
        self.controls = {"freq": 440.0, "fm_depth": 0.5}
        self.inputs = {"fm": {"type": "cv", "group": CV_GROUP, "src": None}}
        self.outputs = {"audio": {"type": "audio", "group": AUDIO_GROUP}}
        self.led_states = {"fm": LedState.OFF, "audio": LedState.OFF}
        self.cv_buffer = np.zeros(1024, dtype=np.float32)
        self.cv_phase = 0
        self.osc_phase = 0.0
        self._audio_thread = None
        # Event-driven vars (create before GUI to avoid AttributeError)
        self.freq_var = tk.DoubleVar(value=self.controls["freq"])
        self.fm_var = tk.DoubleVar(value=self.controls["fm_depth"])
        self._setup_gui(parent_root)
        # Set root after GUI for after() chains
        self.set_root(self.root)
        self.start_sending()

    def _setup_gui(self, parent_root):
        self.root = tk.Toplevel(parent_root) if parent_root else tk.Tk()
        self.root.title(f"Osc {self.module_id}")
        ttk.Button(self.root, text="FM Input", command=lambda: self.connect_input("fm")).pack(pady=5)
        ttk.Button(self.root, text="Audio Output", command=lambda:   self.initiate_connect("audio")).pack(pady=5)
        # In _setup_gui (~L50-60): Add command= to scales
        self.freq_scale = ttk.Scale(self.root, from_=20, to=20000, orient="horizontal", 
                                    variable=self.freq_var, command=self._on_freq_change)
        self.freq_scale.pack(pady=5)
        self.fm_scale = ttk.Scale(self.root, from_=0, to=1, orient="horizontal", 
                                  variable=self.fm_var, command=self._on_fm_change)
        self.fm_scale.pack(pady=5)
        self.gui_leds["fm"] = tk.Label(self.root, text="FM LED", bg="gray", width=10, height=1)
        self.gui_leds["fm"].pack(pady=2)
        self.gui_leds["audio"] = tk.Label(self.root, text="Audio LED", bg="gray", width=10, height=1)
        self.gui_leds["audio"].pack(pady=2)
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        

    # osc_module.py (~L60-80): Optional—take val, drop *args/get()
    def _on_freq_change(self, val):
        try:
            fval = float(val)
            if abs(fval - self.controls["freq"]) < HYSTERESIS:
                return
            self.controls["freq"] = fval
            logger.info(f"Osc {self.module_id}: Freq set to {fval}")
        except ValueError:
            pass

    def _on_fm_change(self, val):
        try:
            fval = float(val)
            if abs(fval - self.controls["fm_depth"]) < HYSTERESIS:
                return
            self.controls["fm_depth"] = fval
            logger.info(f"Osc {self.module_id}: FM depth set to {fval}")
        except ValueError:
            pass

    def _sync_ui(self):
        # Silent set (no trace fire)
        self.freq_var.set(self.controls["freq"])
        self.fm_var.set(self.controls["fm_depth"])
        super()._sync_ui()  # LEDs

    def _start_receiver(self, io, group):
        if io == "fm" and group:
            threading.Thread(target=self._cv_receiver_stub, args=(group,), daemon=True).start()

    def _cv_receiver_stub(self, group):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        sock.bind(('', UDP_AUDIO_PORT))
        mreq = struct.pack("4sl", socket.inet_aton(group), socket.INADDR_ANY)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        sock.settimeout(RECV_TIMEOUT)
        logger.info(f"CV receiver started for fm on {group}")
        while True:
            try:
                data, _ = sock.recvfrom(4)
                cv_val = struct.unpack('<f', data)[0]
                self.cv_buffer[self.cv_phase % len(self.cv_buffer)] = cv_val
                self.cv_phase += 1
            except socket.timeout:
                pass
            except Exception as e:
                logger.warning(f"CV receiver error: {e}")

    def start_sending(self):
        if self._audio_thread is None or not self._audio_thread.is_alive():
            self.led_states["audio"] = LedState.SOLID  # Atomic
            self._queue_led_update("audio", LedState.SOLID)
            # self._audio_thread = threading.Thread(target=self._audio_loop, daemon=True)  # Comment for protocol mode
            # self._audio_thread.start()
            logger.info("Audio sender ready (protocol mode - no send)")

    # Comment _audio_loop for testing
    # def _audio_loop(self): ...  # Comment entire for no UDP spam
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
                controls_copy = self.controls.copy()  # Lock-free snapshot
                fm_depth = controls_copy["fm_depth"]
                base_freq = controls_copy["freq"]
                buffer_idx = (block_phase + i) // (SAMPLE_RATE // 1000) % len(self.cv_buffer)
                cv_now = self.cv_buffer[buffer_idx]
                fm_mod = cv_now * fm_depth
                freq = base_freq * (2 ** fm_mod)
                self.osc_phase += 2 * math.pi * freq / SAMPLE_RATE
                self.osc_phase %= 2 * math.pi
                sample = math.sin(self.osc_phase)
                int_sample = int(sample * 8388607.0)
                if int_sample < 0:
                    int_sample += (1 << 24)
                samples.append(int_sample)
            data = b''.join(bytes([(s >> 16) & 0xFF, (s >> 8) & 0xFF, s & 0xFF]) for s in samples)
            try:
                sock.sendto(data, dest)
            except Exception as e:
                logger.warning(f"Audio send error: {e}")
            time.sleep(BLOCK_SIZE / SAMPLE_RATE)

    def on_closing(self):
        super().on_closing()
        if self.root:
            self.root.destroy()

    def handle_msg(self, msg: ProtocolMessage):
        super().handle_msg(msg)