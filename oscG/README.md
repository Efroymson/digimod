# Digital Modular Synthesizer

A real-time audio synthesizer built on the Olimex ESP32-POE-ISO, utilizing Ethernet for UDP-based audio streaming and DaisySP for sound generation.

## Overview

This project implements a modular synthesizer that generates audio waveforms (currently a sawtooth) and streams them via UDP packets to a receiver for playback. The ESP32 handles synthesis and network transmission, while a Python script (audioRecv.py) decodes and plays the audio on a host machine.

- Hardware: Olimex ESP32-POE-ISO
- Framework: ESP-IDF v5.5.1
- Audio Library: DaisySP
- Network: Ethernet with LAN8720 PHY, UDP unicast
- Audio Format: 24-bit PCM, 48kHz, 96 samples per packet (288 bytes)

## Features

- Real-time sawtooth wave generation at 440Hz.
- Ethernet connectivity with 2ms packet timing (500 Hz FreeRTOS tick rate).
- UDP streaming to a unicast address.
- Audio playback via sounddevice on the receiver.

## Installation

### Prerequisites

- ESP-IDF: Install ESP-IDF v5.5.1 or later. Follow the official guide at https://docs.espressif.com/projects/esp-idf/en/latest/esp32/get-started/index.html.
- Python: Python 3.7+ with required libraries.
- Tools: Git, make, and a serial terminal (e.g., idf.py monitor).

### Setup Commands
git clone https://github.com/Efroymson/deigimod
cd digimod/oscG
pip3 install sounddevice numpy
idf.py set-target esp32
idf.py menuconfig
idf.py reconfigure
idf.py build
idf.py -p /dev/cu.usbserial-0001 flash monitor
python3 audioRecv.py


- In menuconfig, set Component config > FreeRTOS > Tick rate (Hz) to 500.
- Ensure Component config > Ethernet > RMII Clock Mode is set to "Output RMII clock from internal".
- Replace /dev/cu.usbserial-0001 with your serial port.

## Usage

- The ESP32 generates a 440Hz sawtooth wave, packs it into 288-byte UDP packets, and sends them every 2ms to 192.168.2.129:5005 (update UDP_IP in main.cpp to your receiver’s IP).
- audioRecv.py listens on port 5005 and plays the audio using sounddevice.
- Monitor output via the serial terminal for debug messages (e.g., "Sent 288 bytes").

## Current Status

- Version: v0.2 (Initial working version with Ethernet, UDP, and audio playback)
- Achievements:
  - Ethernet initialized with LAN8720 PHY on GPIO17 clock output.
  - UDP packets (288 bytes, 96 samples) sent every 2ms.
  - Clear sawtooth playback with occasional OS X-related glitches.
- Known Issues:
  - Occasional audio glitches, possibly due to OS X audio buffering.
  - Watchdog timer previously triggered by excessive logging, now mitigated.

## Development

### Directory Structure

- main/: Main application code (main.cpp).
- components/mynet/: Custom network module (net.c, net.h).
- audioRecv.py: Python receiver script.

### Contributing

1. Fork the repository.
2. Create a feature branch (git checkout -b feature-name).
3. Commit changes (git commit -m "Description").
4. Push to the branch (git push origin feature-name).
5. Open a pull request.

### Future Improvements

- Investigate and fix OS X audio glitches (e.g., buffer size tuning).
- Add more waveforms (e.g., square, triangle) via DaisySP.
- Implement multicast UDP for multiple receivers.
- Optimize memory usage and reintroduce WDT handling if needed.

## License

MIT

## Acknowledgements

- Espressif for ESP-IDF.
- DaisySP for audio synthesis.
- Olimex for the ESP32-POE-ISO hardware.
- Xai for Grok

## Contact

For questions or support, contact robert@efroymson.com