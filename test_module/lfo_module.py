# lfo_module.py
import tkinter as tk
from tkinter import ttk
import threading
import time
import math
import struct
import socket
from socket import inet_aton
import logging
from base_module import BaseModule, LedState, ProtocolMessage, ProtocolMessageType, CONTROL_MULTICAST, UDP_CONTROL_PORT
from connection_protocol import ConnectionProtocol
from patch_protocol import PatchProtocol

logger = logging.getLogger(__name__)

UDP_CV_PORT = 5005

class LfoModule(PatchProtocol, ConnectionProtocol, BaseModule):
    def __init__(self, mod_id: str, parent_root: tk.Tk = None):
        super().__init__(mod_id, "lfo")
        self.control_ranges = {"rate": [0.1, 10]}
        self.controls = {"rate": 1.0}
        self.inputs = {}
        self.outputs = {"cv": {"type": "cv", "group": f"239.100.3.{hash(mod_id) % 256}"}}
        self.led_states = {"cv": LedState.OFF}
        self.phase = 0.0
        self._cv_thread = threading.Thread(target=self._cv_loop, daemon=True)
        self._setup_gui(parent_root)
        self.start_sending()

    def _setup_gui(self, parent_root):
        self.root = tk.Toplevel(parent_root) if parent_root else tk.Tk()
        self.root.title(f"LFO {self.module_id}")
        cv_button = ttk.Button(self.root, text="CV Output", command=lambda: self.initiate_connect("cv"))
        cv_button.pack()
        self.rate_scale = ttk.Scale(self.root, from_=0.1, to=10, orient="horizontal", command=self.set_rate)
        self.rate_scale.set(self.controls["rate"])
        self.rate_scale.pack()
        self.gui_leds["cv"] = tk.Label(self.root, text="CV LED", bg="gray")
        self.gui_leds["cv"].pack()
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def set_rate(self, val):
        self.controls["rate"] = float(val)
        logger.info(f"Rate set to {self.controls['rate']}")

    def _update_display(self):
        super()._update_display()
        controls_copy = self.controls.copy()
        self.rate_scale.set(controls_copy.get("rate", 1.0))

    def start_sending(self):
        if not self._cv_thread.is_alive():
            with self.lock:
                self.led_states["cv"] = LedState.SOLID
            self._cv_thread.start()

    def _cv_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        ttl = struct.pack('b', 1)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, ttl)
        group = self.outputs["cv"]["group"]
        dest = (group, UDP_CV_PORT)
        LFO_RATE = 1000
        while True:
            with self.lock:
                rate = self.controls["rate"]
            inc = 2 * math.pi * rate / LFO_RATE
            self.phase += inc
            self.phase %= 2 * math.pi
            cv = math.sin(self.phase) * 0.5
            data = struct.pack('<f', cv)
            sock.sendto(data, dest)
            logger.debug(f"Sent CV {cv} to {group}")
            time.sleep(1 / LFO_RATE)

    def on_closing(self):
        super().on_closing()
        self.root.destroy()

    def handle_msg(self, msg: ProtocolMessage):
        super().handle_msg(msg)