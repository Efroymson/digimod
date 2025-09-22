import socket
import time
import sounddevice as sd
import numpy as np
import struct

# Configuration
UDP_PORT = 5005     # Match main.cpp UDP_PORT
PACKET_SIZE = 288   # 96 samples * 3 bytes/sample = 288 bytes
SAMPLE_RATE = 48000 # Match main.cpp SAMPLE_RATE
CHANNELS = 1        # Mono

# Create UDP socket and bind to multicast group
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
# Bind to all interfaces and port (multicast will filter group)
sock.bind(("", UDP_PORT))

# Request multicast address (placeholder; simulate by reading from ESP32 output for now)
# In a real protocol, this would query the DUT (e.g., via a control packet).
# For testing, assume we parse the printed address (e.g., from ESP32 console).
# Here, we'll compute it from a placeholder unicast IP (replace with actual logic later).
placeholder_unicast_ip = "192.168.2.150"  # Example; replace with ESP32's IP or fetch dynamically
octets = placeholder_unicast_ip.split('.')
multicast_ip = f"239.100.{octets[2]}.{octets[3]}"
print(f"Listening on multicast group {multicast_ip}:{UDP_PORT}...")

# Join the multicast group
mreq = struct.pack("4s4s", socket.inet_aton(multicast_ip), socket.inet_aton("0.0.0.0"))
sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

# Initialize sounddevice output
stream = sd.OutputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype='int32')
stream.start()

try:
    while True:
        data, addr = sock.recvfrom(PACKET_SIZE)
        if len(data) == PACKET_SIZE:
            samples = []
            for i in range(0, PACKET_SIZE, 3):
                unsigned_val = struct.unpack('>I', b'\x00' + data[i:i+3])[0]
                if unsigned_val >= (1 << 23):
                    unsigned_val -= (1 << 24)
                samples.append(unsigned_val)
            stream.write(np.array(samples, dtype=np.int32))
        else:
            print(f"Invalid packet size: {len(data)} bytes")

except KeyboardInterrupt:
    stream.stop()
    stream.close()
    sock.close()