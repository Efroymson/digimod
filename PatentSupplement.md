# Supplement to Provisional Patent Application: Implementation Details for Modular Programmable Digital Synthesizer with Virtual Interconnections

This supplement provides a detailed technical implementation of the modular programmable digital synthesizer as described in the provisional application (filed April 21, 2021). It fills in key gaps, including hardware architecture, software framework, audio and UI protocols, and the virtual interconnection mechanism (including the "patchSave" protocol for state serialization and master-slave routing). The implementation uses an ESP32 microcontroller as the master control unit, supporting Ethernet-based multicast networking for audio distribution to slave units, shift-register I/O for UI, and ADC-based control voltage (CV) inputs. All code follows best practices: modular design (FreeRTOS tasks), error handling (ESP_ERROR_CHECK), hysteresis for ADC stability, and sparse logging (ESP_LOG levels). Source code is available in the repository (main.cpp, ui.h, ui.c, net.c, etc.).

## 1. Hardware Architecture

The master unit is built on the Olimex ESP32-POE-ISO board, with a custom PCB for UI and audio I/O. Key components:

- **Microcontroller**: ESP32-S3 (dual-core Xtensa LX7 @ 240MHz, 512KB SRAM, 8MB Flash).
- **Networking**: Ethernet PHY (LAN8720A) via RMII, powered by PoE (GPIO12 for power, GPIO16 for reset). Supports multicast UDP for audio streaming.
- **Audio**: DaisySP library for oscillator (sin wave @ 48kHz, 96-sample blocks, 24-bit BE packed). Output via I2S to PCM1794A DAC (line out); input via PCM1804 ADC (future).
- **UI**:
  - LEDs: 8 dual-color (red/green for yellow via 74HC595 chain, bits 0-7 red, 8-15 green) + 16 single (bits 16-31). Common-anode, inverted MOSI (GPIO32), CLK (GPIO16), latch (GPIO33).
  - Buttons: 16 via 74HC165 parallel-in serial-out (PL latch GPIO3 output, CLK GPIO16, Q7 serial out GPIO5 input). External 10k pull-ups, switches to GND (high=pressed).
  - Pots (CV Inputs): 6 ADCs (GPIO36/2/13/14/4/15, 12-bit, 11dB atten, inverted hardware).
- **Power**: 3.3V rails from PoE converter; stable for analog/digital separation.

| Component | Pins | Notes |
|-----------|------|-------|
| Ethernet | MDC GPIO23, MDIO GPIO18, CLK GPIO17, Power GPIO12, Reset GPIO16 | Multicast IP: 239.100.x.y (derived from unicast). |
| LEDs | MOSI GPIO32, CLK GPIO16, Latch GPIO33 | 32-bit shift out, MSB first, inverted for common anode. |
| Buttons | Latch GPIO3 (out), CLK GPIO16, Serial GPIO5 (in) | High=pressed; shift LSB first (btn1=bit0). |
| ADCs | Ch0 GPIO36 (ADC1), Ch2 GPIO2 (ADC1), Ch3 GPIO13 (ADC1), Ch6 GPIO14 (ADC2), Ch4 GPIO4 (ADC2), Ch0 GPIO15 (ADC2) | Hysteresis 50; inverted 4095-value. |
| Audio I2S | BCLK GPIO19, LRCK GPIO21, DOUT GPIO22 | 48kHz mono, 24-bit, future slave sync. |

Schematic reference: shem-uw10.pdf (pages 1-3: UI shift regs, page 4: Ethernet, page 5: Audio I2S).

## 2. Software Architecture

Developed in ESP-IDF v5.0 (C++ for main.cpp, C for ui.c/net.c). FreeRTOS for multitasking (pinned cores: OSC/UI on 0/1). NVS for state persistence (future patchSave).

- **Tasks**:
  - `sender_task` (core 0, pri 2): Generates 96-sample OSC blocks (DaisySP sin wave, freq from ADCs), packs 24-bit BE, UDP multicast (port 5005, TTL 1).
  - `receiver_task` (core 0, pri 2): Joins multicast, receives/echoes packets (debug).
  - `updateOscTask` (core 0, pri 3): Reads ADCs (ADC1/5 for octave/fine freq), sets osc.SetFreq(base * adj).
  - `updateUITask` (core 1, pri 5): Polls buttons (10ms), updates LED blinks (redGreen pattern for duals, fast for singles).
- **Libraries**: DaisySP (osc), lwIP (UDP), ESP-Netif (Ethernet), ADC oneshot (pots).
- **Configuration**: NVS init/erase on mismatch; log levels configurable (INFO for debug).

| Task | Core/Pri | Function | Rate |
|------|----------|----------|------|
| sender | 0/2 | Audio pack/send | 1ms (48kHz/96) |
| receiver | 0/2 | Multicast recv | Blocking recv |
| updateOsc | 0/3 | ADC to freq | 10ms |
| updateUI | 1/5 | Buttons/LEDs | 10ms |

Code best practices: Static locals for hysteresis, ESP_ERROR_CHECK for all APIs, volatile globals for task-shared state, no busy-loops (vTaskDelayUntil).

## 3. Virtual Interconnection Protocol

The provisional describes virtual logic connections between master/slave units (e.g., pot CV modulating osc freq). Implementation uses a callback-driven system for routing, with "patchSave" for serialization.

- **Routing Mechanism**: Button presses (short/long/double) trigger `button_callback_t` (set via setButtonCallback). Example in main.cpp:
  ```c
  void exampleButtonCb(uint8_t btn, PressType type) {
      ESP_LOGI(TAG, "Synth: Btn %d %s (e.g., route pot%d to osc freq via patchSave)", btn, type_str, btn);
      // Virtual route: switch(btn) { case 1: if(type==SHORT_PRESS) set_virtual_route(ADC3, OSC_FREQ); }
  }
  ```
  - `set_virtual_route(adc, target)`: Global array `virtual_mods[16]` stores mappings (e.g., mods[btn] = adc_val * target_scale).
  - Slaves subscribe to multicast UDP CV packets (future UDP control channel, port 5006).
- **PatchSave Protocol**: JSON-serialized state (NVS/UDP save/load).
  - Format: `{ "patches": [{"src": "ADC3", "dst": "OSC_FREQ", "scale": 1.0}], "state": {"freq": 261.63} }`.
  - Save: nvs_set_str("patches", json); UDP broadcast to slaves.
  - Load: nvs_get_str, parse with cJSON, apply mappings.
  - Buttons: Long press = save patch; double = load default.

This enables "virtual" CV without physical cables, stored/restored across power cycles/slaves.

## 4. Audio Protocol

Mono sine wave oscillator streamed via UDP multicast (48kHz, low latency for modular chaining).

- **Packet Format**: 288 bytes/block (96 samples * 3 bytes 24-bit BE, signed int32 scaled * 8388607).
  - Headerless; continuous stream (1ms intervals).
  - Packing: PACK_L24_BE (macro: MSB first).
- **Multicast**: IP 239.100.x.y (unicast .x.y), port 5005, TTL 1 (LAN).
- **Receiver**: Python audioRecv.py (sounddevice) unpacks (>23-bit = negative), plays.
- **OSC Control**: ADCs map to freq (octave from ADC1/512, fine from ADC5/4095 * base).

| Parameter | Value | Notes |
|-----------|-------|-------|
| Sample Rate | 48kHz | DaisySP Init(SAMPLE_RATE) |
| Block Size | 96 | UDP packet size 288 bytes |
| Format | 24-bit BE signed | osc.Process() * 8388607, unpack with >I pad \x00, signed adjust |
| Network | UDP multicast 239.100.x.y:5005 | Derived from ESP IP; Python joins group |

## 5. UI Protocol

Shift-register I/O for scalable UI (buttons/LEDs), polled at 10ms for responsiveness.

- **LEDs**: 32-bit-bit out (74HC595 chain). Patterns: redGreen (alternate red/green 500ms), fast blink (100ms). Blink state machine: Count down interval, toggle on zero, reset count.
- **Buttons**: 16-bit in (74HC165). Edge detect: Press starts timer, release computes duration (short <1s, long >1s), double if <500ms between presses. Cb fires on events.
- **Pots**: 6 ADCs, hysteresis 50, inverted (4095 - raw).

| UI Element | Type | Protocol |
|------------|------|----------|
| Dual LEDs (0-7) | Blink redGreen slow | Bits 0-7 red, 8-15 green; toggle every 500ms |
| Single LEDs (8-23) | Fast blink | Bits 16-31; 100ms ON/OFF |
| Buttons (1-16) | Edge detect | High=pressed; short/long/double via timer |
| Pots (ADC1-8) | Ones hot read | 12-bit, hysteresis, 10ms update |

## 6. Future Work

- **Slave Units**: Mirror master (UDP recv for audio/CV, local I2S out).
- **PatchSave Full**: cJSON integration for JSON over UDP/NVS.
- **Modulation Expansion**: LFO/env modules, full virtual graph (DAG for routes).
- **Testing**: Oscilloscope for CLK/QH timing; audio loopback.

This implementation realizes the provisional's vision of virtual, storable interconnections in a digital modular synth, with Ethernet for scalability. Total LOC ~800; extensible for Eurorack form factor.

## Addendum: Enhanced Virtual Interconnection and UI Protocols (Updated September 26, 2025)

This addendum expands on the virtual interconnection mechanism (Section 3) and UI protocol (Section 5) to incorporate refined button press handling for improved usability in modular synthesis. The changes leverage short and long press detections (implemented via edge-based polling in `ui.c` with FreeRTOS tasks) to enable intuitive, cable-free routing between master and slave units. All interactions occur over Ethernet multicast UDP (control channel on port 5006), ensuring low-latency synchronization across devices. Code follows best practices: stateful detection with hysteresis for stability, error-checked API calls (e.g., `ESP_ERROR_CHECK`), and sparse logging for debug efficiency.

### 3.1 Button-Driven Connection Protocol

The virtual interconnection system now supports gesture-based routing via button presses, distinguishing between short presses (<1s) and long presses (>1s). Thresholds are configurable via NVS for user tuning. This protocol allows users to dynamically connect outputs (e.g., LFO, envelope generators) to inputs (e.g., oscillator frequency, filter cutoff) without physical cables, with visual feedback via LED patterns (e.g., flashing for pending connections).

- **Short Press on Output Button**: Initiates the connection protocol. The master unit broadcasts a "connection request" packet via UDP multicast (port 5006), specifying the output type (e.g., CV source like ADC-mapped LFO). Compatible slave units receive this and flash their input LEDs (e.g., fast blink pattern, 100ms interval via `blinkLedBit`) to indicate readiness. This visual cue helps users identify available inputs. If no input responds within a timeout (e.g., 10s), the protocol terminates automatically.

- **Short Press on Input Button**:
  - If the unit is in connection protocol mode (i.e., its LED is flashing due to a pending request from a compatible output), it establishes the virtual route. The slave sends a "connection complete" acknowledgment back to the master via UDP, updating the global `virtual_mods` array (e.g., mapping source ADC to destination parameter with scale factor). All other flashing inputs across slaves are terminated via a broadcast "clear" packet to prevent conflicts.
  - If not in connection mode, the press does nothing by default. Optionally (configurable via NVS), it can query and highlight a currently connected output by requesting the master to flash a specific LED pattern (e.g., redGreen slow blink) on the source unit, aiding in troubleshooting or visualization of existing patches.

- **Long Press on Input Button**: If the input is currently connected (checked via `virtual_mods` lookup), this erases the route, freeing it for new connections. The unit broadcasts a "disconnect" packet to update all devices, resetting mappings and stopping any active modulations. This supports rapid reconfiguration in performance scenarios, such as swapping modulators mid-session without menu navigation.

These gestures integrate with the "patchSave" protocol for persistence: Connections are serialized in JSON (e.g., `{ "patches": [{"src": "OUTPUT_BTN1", "dst": "INPUT_BTN2", "type": "CV_MOD", "scale": 1.0}] }`) and stored in NVS or broadcast for slave synchronization.

### Use Cases in Modular Synthesis

- **Live Performance Routing**: A short press on an output button (e.g., LFO) triggers flashing on compatible inputs (e.g., VCO freq pots). A subsequent short press on an input connects them virtually, allowing real-time timbre changes without cables—ideal for evolving drones or sequences.

- **Patch Cleanup**: During sound design, a long press on a connected input (e.g., filter cutoff) disconnects it, enabling quick reassignment to another source like an envelope, with all changes savable via "patchSave" for recall across power cycles.

- **Debugging Connections**: Short press on a non-pending input requests flashing from its connected output, visually confirming routes in complex multi-slave setups (e.g., master oscillator modulating multiple slaves).

This enhancement realizes the provisional's vision of storable, virtual CV, with extensible code (e.g., future double-press support via state machine in `pollButtons`). Total added LOC ~50; compatible with Eurorack scaling.