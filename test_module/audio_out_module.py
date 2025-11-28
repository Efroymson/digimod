# audio_out_module.py — FINAL WORKING
import tkinter as tk
from tkinter import ttk
import threading
import queue
import time
import numpy as np
import socket
import struct
import logging

from module import Module, KnobSlider

from base_module import BaseModule, LedState, ProtocolMessage, ProtocolMessageType, CONTROL_MULTICAST, UDP_CONTROL_PORT, JackWidget
from connection_protocol import ConnectionProtocol
from patch_protocol import PatchProtocol

logger = logging.getLogger(__name__)

UDP_AUDIO_PORT = 5005
SAMPLE_RATE = 48000
BLOCK_SIZE = 96
PACKET_SIZE = BLOCK_SIZE * 3
AUDIO_GROUP_L = '239.100.2.150'
AUDIO_GROUP_R = '239.100.2.151'

class AudioOutModule(ConnectionProtocol, PatchProtocol, BaseModule):
    def __init__(self, mod_id: str, parent_root: tk.Tk = None):
        super().__init__(mod_id, "audio_out")  # FIRST — no mcast_group needed

        self.inputs = {
            "left": {"type": "audio", "group": AUDIO_GROUP_L},
            "right": {"type": "audio", "group": AUDIO_GROUP_R}
        }
        self.outputs = {}

        self.control_ranges = {}
        self.controls = {}
        self.left_q = queue.Queue(maxsize=100)
        self.right_q = queue.Queue(maxsize=100)
        self._left_receiver = None
        self._right_receiver = None

        self._setup_gui(parent_root)
        self.set_root(self.root)
        # 3. Initialize connection states (must come after inputs/outputs defined)
        # self._init_connection_states()
        # self._sync_initial_leds()
        self._ensure_io_defs()          # ← NEW
        self._refresh_gui_from_controls()   # ← now comes from ConnectionProtocol
        self._update_display()
        if self.root:
            self.root.update_idletasks()

    def _setup_gui(self, parent_root):
        self.root = tk.Toplevel(parent_root) if parent_root else tk.Tk()
        self.root.title(f"Audio Out {self.module_id}")
        self.gui_leds["left"] = JackWidget(self.root, "left", "Left Input",
                                           short_press_callback=self.connect_input,
                                           long_press_callback=self.long_press_input,
                                           verbose_text=False)
        self.gui_leds["left"].pack(pady=5)
        self.gui_leds["right"] = JackWidget(self.root, "right", "Right Input",
                                            short_press_callback=self.connect_input,
                                            long_press_callback=self.long_press_input,
                                            verbose_text=False)
        self.gui_leds["right"].pack(pady=5)
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def _start_receiver(self, io, group, offset, block_size):
        q = self.left_q if io == "left" else self.right_q
        thread = threading.Thread(target=self._receiver_stub, args=(group, q, io, offset, block_size), daemon=True)
        thread.start()
        if io == "left":
            self._left_receiver = thread
        else:
            self._right_receiver = thread

    def _receiver_stub(self, group, q, io_id, offset, block_size):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, 'SO_REUSEPORT'):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        sock.bind(('', UDP_AUDIO_PORT))
        mreq = struct.pack("4sl", socket.inet_aton(group), socket.INADDR_ANY)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        sock.settimeout(0.1)
        while True:
            try:
                data, _ = sock.recvfrom(PACKET_SIZE)
                if len(data) == PACKET_SIZE:
                    samples = [self._unpack_sample(data[i:i+3]) for i in range(0, len(data), 3)]
                    block = np.array(samples[offset:offset+block_size], dtype=np.int32)
                    try:
                        q.put_nowait(block)
                    except queue.Full:
                        logger.debug(f"{io_id} queue full – drop block")
            except socket.timeout:
                continue
            except Exception as e:
                logger.warning(f"{io_id} receiver error: {e}")

    def _unpack_sample(self, b: bytes) -> int:
        v = (b[0] << 16) | (b[1] << 8) | b[2]
        return v - 0x1000000 if v & 0x800000 else v

    def on_closing(self):
        super().on_closing()
        if self.root:
            self.root.destroy()

    def handle_msg(self, msg: ProtocolMessage):
        super().handle_msg(msg)