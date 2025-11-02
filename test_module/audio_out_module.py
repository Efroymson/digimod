# audio_out_module.py
import tkinter as tk
from tkinter import ttk
import threading
import queue
import time
import numpy as np
import socket
from socket import inet_aton
import struct
import logging
from base_module import BaseModule, LedState, ProtocolMessage, ProtocolMessageType, CONTROL_MULTICAST, UDP_CONTROL_PORT
from connection_protocol import ConnectionProtocol
from patch_protocol import PatchProtocol

# import sounddevice as sd  # Optional, comment out if not used

logger = logging.getLogger(__name__)

UDP_AUDIO_PORT = 5005
SAMPLE_RATE = 48000
BLOCK_SIZE = 96
PACKET_SIZE = BLOCK_SIZE * 3
AUDIO_MULTICAST = '239.100.2.150'

class AudioOutModule(PatchProtocol, ConnectionProtocol, BaseModule):
    def __init__(self, mod_id: str, parent_root: tk.Tk = None):
        super().__init__(mod_id, "audio_out")
        self.control_ranges = {}
        self.controls = {}
        self.inputs = {"left": {"type": "audio", "group": AUDIO_MULTICAST, "src": None},
                       "right": {"type": "audio", "group": '239.100.2.151', "src": None}}
        self.outputs = {}
        self.led_states = {"left": LedState.OFF, "right": LedState.OFF}
        self.left_q = queue.Queue(maxsize=10)
        self.right_q = queue.Queue(maxsize=10)
        self._left_receiver = None
        self._right_receiver = None
        self._setup_gui(parent_root)
        # Optional: self.stream = sd.OutputStream(samplerate=SAMPLE_RATE, channels=2, dtype='int32', blocksize=BLOCK_SIZE, callback=self._audio_callback)
        # self.stream.start()

    def _setup_gui(self, parent_root):
        self.root = tk.Toplevel(parent_root) if parent_root else tk.Tk()
        self.root.title(f"Audio Out {self.module_id}")
        left_button = ttk.Button(self.root, text="Left Input", command=lambda: self.connect_input("left"))
        left_button.pack()
        right_button = ttk.Button(self.root, text="Right Input", command=lambda: self.connect_input("right"))
        right_button.pack()
        self.gui_leds["left"] = tk.Label(self.root, text="Left LED", bg="gray")
        self.gui_leds["left"].pack()
        self.gui_leds["right"] = tk.Label(self.root, text="Right LED", bg="gray")
        self.gui_leds["right"].pack()
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def _update_display(self):
        super()._update_display()

    def _start_receiver(self, io, group):
        if group:
            q = self.left_q if io == "left" else self.right_q
            key = "_left_receiver" if io == "left" else "_right_receiver"
            thread = getattr(self, key)
            if thread and thread.is_alive():
                return
            new_thread = threading.Thread(target=self._receiver_stub, args=(group, q, io), daemon=True)
            new_thread.start()
            setattr(self, key, new_thread)

    def _receiver_stub(self, group, q, io):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        sock.bind(('', UDP_AUDIO_PORT))
        mreq = struct.pack("4sl", inet_aton(group), socket.INADDR_ANY)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        sock.settimeout(RECV_TIMEOUT)
        logger.info(f"Audio receiver started for {io} on {group}")
        while True:
            try:
                data, _ = sock.recvfrom(PACKET_SIZE + 1)
                if len(data) == PACKET_SIZE:
                    samples = []
                    for i in range(0, PACKET_SIZE, 3):
                        sample_data = data[i:i+3]
                        unsigned_val = struct.unpack('>I', b'\x00' + sample_data)[0]
                        if unsigned_val >= (1 << 23):
                            unsigned_val -= (1 << 24)
                        samples.append(unsigned_val)
                    try:
                        q.put_nowait(np.array(samples, dtype=np.int32))
                    except queue.Full:
                        logger.debug("Queue full, drop packet")
                else:
                    q.put(np.zeros(BLOCK_SIZE, dtype=np.int32))
            except socket.timeout:
                q.put(np.zeros(BLOCK_SIZE, dtype=np.int32))
            except Exception as e:
                logger.warning(f"Audio receiver error {io}: {e}")
                q.put(np.zeros(BLOCK_SIZE, dtype=np.int32))

    # Optional callback
    # def _audio_callback(self, outdata, frames, time, status):
    #     left = self.left_q.get() if not self.left_q.empty() else np.zeros(frames, dtype=np.int32)
    #     right = self.right_q.get() if not self.right_q.empty() else np.zeros(frames, dtype=np.int32)
    #     outdata[:, 0] = left
    #     outdata[:, 1] = right

    def on_closing(self):
        # Optional: self.stream.stop()
        # self.stream.close()
        super().on_closing()
        self.root.destroy()

    def handle_msg(self, msg: ProtocolMessage):
        super().handle_msg(msg)