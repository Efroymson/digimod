import socket
import time
import struct
import sounddevice as sd
import numpy as np

# Configuration
UDP_IP = "0.0.0.0"  # Listen on all interfaces
UDP_PORT = 5005     # Match main.cpp UDP_PORT
PACKET_SIZE = 288   # 96 samples * 3 bytes/sample = 288 bytes
SAMPLE_RATE = 48000 # Match main.cpp SAMPLE_RATE
CHANNELS = 1        # Mono

# Create UDP socket
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(("", UDP_PORT))

print(f"UDP receiver listening on {UDP_IP}:{UDP_PORT}...")

# Initialize sounddevice output
stream = sd.OutputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype='int32')
stream.start()

try:
    while True:
        data, addr = sock.recvfrom(PACKET_SIZE)
        if len(data) == PACKET_SIZE:
            # Unpack 3-byte 24-bit signed ints (big-endian, AES67 L24)
            samples = []
            for i in range(0, PACKET_SIZE, 3):
                # Use struct to unpack big-endian 24-bit signed int
                value = struct.unpack('>i', b'\x00' + data[i:i+3])[0]  # Prepend 0x00 to make 32-bit, unpack as signed big-endian
                samples.append(value)
            # print(f"Received {len(samples)} samples from {addr}: {samples[:5]}...")  # Print first 5 for debug
            # Play audio
            stream.write(np.array(samples, dtype=np.int32))
            # time.sleep(len(samples) / SAMPLE_RATE)  # Wait for playback
        else:
            print(f"Invalid packet size: {len(data)} bytes")
            
except KeyboardInterrupt:
    stream.stop()
    stream.close()
    sock.close()