import socket
import struct
import sounddevice as sd

# Configuration
UDP_IP = "0.0.0.0"  # Listen on all interfaces
UDP_PORT = 5005     # Match main.cpp UDP_PORT
BLOCK_SIZE = 96     # Match main.cpp BLOCK_SIZE
SAMPLE_RATE = 48000 # Match main.cpp SAMPLE_RATE

# Create UDP socket
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # Allow reuse
sock.bind((UDP_IP, UDP_PORT))
print(f"UDP receiver listening on {UDP_IP}:{UDP_PORT}...")

try:
    while True:
        # Receive packet (384 bytes for 96 floats)
        data, addr = sock.recvfrom(BLOCK_SIZE * 4)  # 4 bytes per float
        if len(data) == BLOCK_SIZE * 4:
            # Unpack raw bytes to float array
            samples = struct.unpack('96f', data)
            print(f"Received {len(samples)} samples from {addr}: {samples[:5]}...")  # Print first 5 for debug

            # Play audio using sounddevice
            sd.play(samples, SAMPLE_RATE)
            time.sleep(BLOCK_SIZE / SAMPLE_RATE)  # Wait for playback to finish
        else:
            print(f"Invalid packet size: {len(data)} bytes")

except KeyboardInterrupt:
    print("Stopped by user")
finally:
    sock.close()
