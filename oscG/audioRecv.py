import socket
import argparse
import time
import sounddevice as sd
import numpy as np
import struct
import queue
import threading
import logging

# Setup logging for debug
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Configuration
UDP_PORT = 5005     # Match main.cpp UDP_PORT
PACKET_SIZE = 288   # 96 samples * 3 bytes/sample = 288 bytes
SAMPLE_RATE = 48000 # Match main.cpp SAMPLE_RATE
BLOCK_SIZE = 96     # Samples per packet
CHANNELS = 2        # Stereo
RECV_TIMEOUT = 0.001  # 1ms for low latency

def parse_args():
    parser = argparse.ArgumentParser(description="Stereo UDP multicast audio receiver for Digital Modular Synthesizer.")
    parser.add_argument('--multicast1', type=str, default='239.100.2.150', help="Multicast IP for left channel (e.g., from ESP32 console).")
    parser.add_argument('--multicast2', type=str, default='239.100.2.151', help="Multicast IP for right channel.")
    return parser.parse_args()

def unpack_sample(data):
    """Unpack 24-bit BE signed sample."""
    unsigned_val = struct.unpack('>I', b'\x00' + data)[0]
    if unsigned_val >= (1 << 23):
        unsigned_val -= (1 << 24)
    return unsigned_val

def receiver_thread(multicast_ip, q):
    """Thread to receive from one multicast and queue samples."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)  # Added for macOS/multiple binds
        sock.bind(("", UDP_PORT))  # Bind to same port for both
        mreq = struct.pack("4s4s", socket.inet_aton(multicast_ip), socket.inet_aton("0.0.0.0"))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        sock.settimeout(RECV_TIMEOUT)
        logging.info(f"Receiver started for {multicast_ip}")
    except OSError as e:
        logging.error(f"Socket setup failed for {multicast_ip}: {e}")
        return  # Exit thread on fatal error

    while True:
        try:
            data, _ = sock.recvfrom(PACKET_SIZE)
            if len(data) == PACKET_SIZE:
                samples = [unpack_sample(data[i:i+3]) for i in range(0, PACKET_SIZE, 3)]
                q.put(samples)
            else:
                q.put([0] * BLOCK_SIZE)  # Zero fill on invalid
        except socket.timeout:
            q.put([0] * BLOCK_SIZE)  # Zero fill on timeout
        except Exception as e:
            logging.warning(f"Receiver error ({multicast_ip}): {e}")
            q.put([0] * BLOCK_SIZE)  # Continue with zeros

def main():
    args = parse_args()
    print(f"Listening on left: {args.multicast1}:{UDP_PORT}, right: {args.multicast2}:{UDP_PORT}...")

    # Queues for left/right samples
    left_q = queue.Queue(maxsize=10)
    right_q = queue.Queue(maxsize=10)

    # Start receiver threads
    threading.Thread(target=receiver_thread, args=(args.multicast1, left_q), daemon=True).start()
    time.sleep(0.1)  # Slight delay to avoid race on bind
    threading.Thread(target=receiver_thread, args=(args.multicast2, right_q), daemon=True).start()

    # Stereo output stream
    stream = sd.OutputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype='int32')
    stream.start()

    try:
        while True:
            # Get samples from queues (block if empty)
            left_samples = np.array(left_q.get(), dtype=np.int32)
            right_samples = np.array(right_q.get(), dtype=np.int32)
            stereo_samples = np.column_stack((left_samples, right_samples))
            stream.write(stereo_samples)
    except KeyboardInterrupt:
        stream.stop()
        stream.close()

if __name__ == "__main__":
    main()
