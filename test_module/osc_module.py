# osc_module.py
import tkinter as tk
from tkinter import ttk
import threading
import time
import math
import numpy as np
import socket
from socket import inet_aton
import struct
import logging
from typing import Dict
from base_module import BaseModule, LedState, ProtocolMessage, ProtocolMessageType, CONTROL_MULTICAST, UDP_CONTROL_PORT
from connection_protocol import ConnectionProtocol
from patch_protocol import PatchProtocol

logger = logging.getLogger(__name__)

UDP_AUDIO_PORT = 5005
SAMPLE_RATE = 48000
BLOCK_SIZE = 96
PACKET_SIZE = BLOCK_SIZE * 3
AUDIO_MULTICAST = '239.100.2.150'

class OscModule(PatchProtocol, ConnectionProtocol, BaseModule):
    def __init__(self, mod_id: str, parent_root: tk.Tk = None):
        super().__init__(mod_id, "osc")
        self.control_ranges = {"freq": [20, 20000], "fm_depth": [0, 1]}
        self.controls = {"freq": 440, "fm_depth": 0.5}
        self.inputs = {"fm": {"type": "cv", "group": None, "src": None}}
        self.outputs = {"audio": {"type": "audio", "group": AUDIO_MULTICAST}}
        self.led_states = {"fm": LedState.OFF, "audio": LedState.OFF}
        self.cv_buffer = np.zeros(1024, dtype=float)
        self.cv_phase = 0
        self.osc_phase = 0.0
        self._audio_thread = threading.Thread(target=self._audio_loop, daemon=True)
        self._setup_gui(parent_root)
        self.start_sending()

    def _setup_gui(self, parent_root):
        self.root = tk.Toplevel(parent_root) if parent_root else tk.Tk()
        self.root.title(f"Osc {self.module_id}")
        fm_button = ttk.Button(self.root, text="FM Input", command=lambda: self.connect_input("fm"))
        fm_button.pack()
        audio_button = ttk.Button(self.root, text="Audio Output", command=lambda: self.initiate_connect("audio"))
        audio_button.pack()
        self.freq_scale = ttk.Scale(self.root, from_=20, to=20000, orient="horizontal", command=self.set_freq)
        self.freq_scale.set(self.controls["freq"])
        self.freq_scale.pack()
        self.fm_scale = ttk.Scale(self.root, from_=0, to=1, orient="horizontal", command=self.set_fm_depth)
        self.fm_scale.set(self.controls["fm_depth"])
        self.fm_scale.pack()
        self.gui_leds["fm"] = tk.Label(self.root, text="FM LED", bg="gray")
        self.gui_leds["fm"].pack()
        self.gui_leds["audio"] = tk.Label(self.root, text="Audio LED", bg="gray")
        self.gui_leds["audio"].pack()
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def set_freq(self, val):
        self.controls["freq"] = float(val)
        logger.info(f"Freq set to {self.controls['freq']}")

    def set_fm_depth(self, val):
        self.controls["fm_depth"] = float(val)
        logger.info(f"FM depth set to {self.controls['fm_depth']}")

    def _update_display(self):
        super()._update_display()
        controls_copy = self.controls.copy()
        self.freq_scale.set(controls_copy.get("freq", 440))
        self.fm_scale.set(controls_copy.get("fm_depth", 0.5))

    def _start_receiver(self, io, group):
        if io == "fm" and group:
            threading.Thread(target=self._cv_receiver_stub, args=(group,), daemon=True).start()

    def _cv_receiver_stub(self, group):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        sock.bind(('', UDP_AUDIO_PORT))
        mreq = struct.pack("4sl", inet_aton(group), socket.INADDR_ANY)
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
        if not self._audio_thread.is_alive():
            with self.lock:
                self.led_states["audio"] = LedState.SOLID
            self._audio_thread.start()

    def _audio_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        ttl = struct.pack('b', 1)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, ttl)
        dest = (AUDIO_MULTICAST, UDP_AUDIO_PORT)
        while True:
            samples = []
            for i in range(BLOCK_SIZE):
                with self.lock:
                    fm_depth = self.controls["fm_depth"]
                    base_freq = self.controls["freq"]
                buffer_idx = self.osc_phase // (SAMPLE_RATE / 1000) % len(self.cv_buffer)
                cv_now = float(self.cv_buffer[buffer_idx])
                fm_mod = cv_now * fm_depth
                freq = base_freq * math.pow(2, fm_mod)
                self.osc_phase += 2 * math.pi * freq / SAMPLE_RATE
                self.osc_phase %= 2 * math.pi
                sample = math.sin(self.osc_phase)
                int_sample = int(sample * 8388607.0)
                if int_sample < 0:
                    int_sample += (1 << 24)
                samples.append(int_sample)
            data = b''
            for s in samples:
                data += bytes([(s >> 16) & 0xFF, (s >> 8) & 0xFF, s & 0xFF])
            sock.sendto(data, dest)
            logger.debug(f"Sent {len(data)} bytes audio")
            time.sleep(BLOCK_SIZE / SAMPLE_RATE)

    def on_closing(self):
        super().on_closing()
        self.root.destroy()

    def handle_msg(self, msg: ProtocolMessage):
        super().handle_msg(msg)