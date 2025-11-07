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
        self.start_sending()

    def _setup_gui(self, parent_root):
        self.root = tk.Toplevel(parent_root) if parent_root else tk.Tk()
        self.root.title(f"LFO {self.module_id}")
        ttk.Button(self.root, text="CV Output", command=lambda: self.initiate_connect("cv")).pack(pady=5)
        self.rate_scale = ttk.Scale(self.root, from_=0.1, to=10, orient="horizontal", 
                                    variable=self.rate_var, command=self._on_rate_change)  # ← Add this: command=
        self.rate_scale.pack(pady=5)
        self.gui_leds["cv"] = tk.Label(self.root, text="CV LED", bg="gray", width=10, height=1)
        self.gui_leds["cv"].pack(pady=2)
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)


            
    # _sync_ui (~L65): Unchanged—silent set (no command fire)
    def _sync_ui(self):
        controls_copy = self.controls.copy()  # Atomic snapshot (if added from prior)
        self.rate_var.set(controls_copy.get("rate", 1.0))  # Programmatic: No callback
        super()._sync_ui()  # Drain LEDs (event-driven queue)

    def start_sending(self):
        if self._cv_thread is None or not self._cv_thread.is_alive():
            self.led_states["cv"] = LedState.SOLID  # Atomic
            self._queue_led_update("cv", LedState.SOLID)  # Push GUI
            self._cv_thread = threading.Thread(target=self._cv_loop, daemon=True)
            self._cv_thread.start()

    def _cv_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        ttl = struct.pack('b', 1)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, ttl)
        group = self.outputs["cv"]["group"]
        dest = (group, UDP_CV_PORT)
        logger.info(f"CV sender started for {self.module_id}")
        while True:
            controls_copy = self.controls.copy()
            rate = controls_copy["rate"]
            inc = 2 * math.pi * rate / LFO_RATE
            self.phase += inc
            self.phase %= 2 * math.pi
            cv = 0.5 * math.sin(self.phase)  # Bipolar -0.5 to 0.5
            data = struct.pack('<f', cv)
            try:
                sock.sendto(data, dest)
                logger.debug(f"LFO {self.module_id}: Sent CV {cv:.3f}")
            except Exception as e:
                logger.warning(f"CV send error: {e}")
            time.sleep(1.0 / LFO_RATE)
            
            
    # Update _on_rate_change (~L50-60): Sig to val (str); DEBUG log (spam-safe)
    def _on_rate_change(self, val):  # Tk passes str(val); no *args needed
        try:
            fval = float(val)
            if abs(fval - self.controls["rate"]) < HYSTERESIS:
                return  # Skip noise (RT: like ADC filter)
            self.controls["rate"] = fval
            logger.info(f"LFO {self.module_id}: Rate set to {fval}")  # DEBUG: Tune to INFO if silent
        except ValueError:
            pass  # Edge: Bad val (rare)
            
    def on_closing(self):
        super().on_closing()
        if self.root:
            self.root.destroy()

    def handle_msg(self, msg: ProtocolMessage):
        super().handle_msg(msg)