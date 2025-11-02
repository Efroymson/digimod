import tkinter as tk
from tkinter import ttk
import threading
import time
import math
import struct
import socket
import logging
from base_module import BaseModule, LedState, ProtocolMessage, ProtocolMessageType, CONTROL_MULTICAST, UDP_CONTROL_PORT
from connection_protocol import ConnectionProtocol
from patch_protocol import PatchProtocol

logger = logging.getLogger(__name__)

UDP_CV_PORT = 5005
CV_GROUP = '239.100.1.1'
LFO_RATE = 1000  # Hz
HYSTERESIS = 0.01

class LfoModule(PatchProtocol, ConnectionProtocol, BaseModule):
    def __init__(self, mod_id: str, parent_root: tk.Tk = None):
        super().__init__(mod_id, "lfo")
        self.control_ranges = {"rate": [0.1, 10]}
        self.controls = {"rate": 1.0}
        self.inputs = {}
        self.outputs = {"cv": {"type": "cv", "group": CV_GROUP}}
        self.led_states = {"cv": LedState.OFF}
        self.phase = 0.0
        self._cv_thread = None
        # Event-driven var (before GUI)
        self.rate_var = tk.DoubleVar(value=self.controls["rate"])
        self._setup_gui(parent_root)
        self.set_root(self.root)
        self.rate_var.trace('w', self._on_rate_change)
        self.start_sending()

    def _setup_gui(self, parent_root):
        self.root = tk.Toplevel(parent_root) if parent_root else tk.Tk()
        self.root.title(f"LFO {self.module_id}")
        ttk.Button(self.root, text="CV Output", command=lambda: self.initiate_connect("cv")).pack(pady=5)
        self.rate_scale = ttk.Scale(self.root, from_=0.1, to=10, orient="horizontal", variable=self.rate_var)
        self.rate_scale.pack(pady=5)
        self.gui_leds["cv"]