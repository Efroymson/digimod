import tkinter as tk
from tkinter import ttk
import threading
import queue
import time
import numpy as np
import socket
import struct
import logging
from base_module import BaseModule, LedState, ProtocolMessage, ProtocolMessageType, CONTROL_MULTICAST, UDP_CONTROL_PORT, RECV_TIMEOUT
from connection_protocol import ConnectionProtocol
from patch_protocol import PatchProtocol

# import sounddevice as sd  # Uncomment for playback + pip install sounddevice

logger = logging.getLogger(__name__)

UDP_AUDIO_PORT = 5005
SAMPLE_RATE = 48000
BLOCK_SIZE = 96
PACKET_SIZE = BLOCK_SIZE * 3
AUDIO_GROUP_L = '239.100.2.150'
AUDIO_GROUP_R = '239.100.2.151'

class AudioOutModule(PatchProtocol, ConnectionProtocol, BaseModule):
    def __init__(self, mod_id: str, parent_root: tk.Tk = None):
        super().__init__(mod_id, "audio_out")
        self.control_ranges = {}
        self.controls = {}
        self.inputs = {"left": {"type": "audio", "group": AUDIO_GROUP_L, "src": None},
                       "right": {"type": "audio", "group": AUDIO_GROUP_R, "src": None}}
        self.outputs = {}
        self.led_states = {"left": LedState.OFF, "right": LedState.OFF}
        self.left_q = queue.Queue(maxsize=10)
        self.right_q = queue.Queue(maxsize=10)
        self._left_receiver = None
        self._right_receiver = None
        self._setup_gui(parent_root)
        self.set_root(self.root)
        # self.stream = sd.OutputStream(samplerate=SAMPLE_RATE, channels=2, dtype='int32', blocksize=BLOCK_SIZE, callback=self._audio_callback)
        # self.stream.start()

    def _setup_gui(self, parent_root):
        self.root = tk.Toplevel(parent_root) if parent_root else tk.Tk()
        self.root.title(f"Audio Out {self.module_id}")
        ttk.Button(self.root, text="Left Input", command=lambda: self.connect_input("left")).pack(pady=5)
        ttk.Button(self.root, text="Right Input", command=lambda: self.connect_input("right")).pack(pady=5)
        self.gui_leds["left"] = tk.Label(self.root, text="Left LED", bg="gray", width=10, height=1)
        self.gui_leds["left"].pack(pady=2)
        self.gui_leds["right"] = tk.Label(self.root, text="Right LED", bg="gray", width=10, height=1)
        self.gui_leds["right"].pack(pady=2)
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def _sync_ui(self):
        super()._sync_ui()  # Only LEDs (no sliders)

    def _start_receiver(self, io, group):
        if not group:
            return
        q = self.left_q if io == "left" else self.right_q
        key = '_left_receiver' if io == "left" else '_right_receiver'
        thread = getattr(self, key)
        if thread and thread.is_alive():
            return
        new_thread = threading.Thread(target=self._receiver_stub, args=(group, q, io), daemon=True)
        new_thread.start()
        setattr(self, key, new_thread)

    # In _receiver_stub (~L80): Robust timeout/full
    def _receiver_stub(self, group, q, io_id):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        sock.bind(('', UDP_AUDIO_PORT))
        mreq = struct.pack("4sl", socket.inet_aton(group), socket.INADDR_ANY)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        sock.settimeout(0.1)  # Longer for sim jitter
        drop_count = 0
        while True:
            try:
                data, _ = sock.recvfrom(PACKET_SIZE)
                if len(data) == PACKET_SIZE:
                    samples = [self._unpack_sample(data[i:i+3]) for i in range(0, len(data), 3)]
                    q.put_nowait(np.array(samples[:BLOCK_SIZE], dtype=np.int32))
                    drop_count = 0
                else:
                    logger.debug(f"Short packet for {io_id}: {len(data)} bytes")
            except socket.timeout:
                drop_count += 1
                if drop_count % 100 == 0:  # Log every ~10s
                    logger.warning(f"{io_id} receiver timeout spam: {drop_count}")
                continue  # No zeros push
            except queue.Full:
                logger.warning(f"{io_id} queue full: Drop block")
                drop_count += 1
                continue  # No raise/block
            except Exception as e:
                logger.warning(f"{io_id} receiver error: {e}")
                drop_count += 1

    # def _audio_callback(self, outdata, frames, time, status):
    #     left = self.left_q.get() if not self.left_q.empty() else np.zeros(frames, dtype=np.int32)
    #     right = self.right_q.get() if not self.right_q.empty() else np.zeros(frames, dtype=np.int32)
    #     outdata[:, 0] = left[:frames]
    #     outdata[:, 1] = right[:frames]

    def on_closing(self):
        # self.stream.stop()
        # self.stream.close()
        super().on_closing()
        if self.root:
            self.root.destroy()

    def handle_msg(self, msg: ProtocolMessage):
        # No lock: Atomic
        super().handle_msg(msg)  # Chain