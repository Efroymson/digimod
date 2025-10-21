#include <stdio.h>
#include <string.h>
#include <math.h>
#include <algorithm>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_system.h"
#include "esp_netif.h"
#include "esp_event.h"
#include "nvs_flash.h"
#include "lwip/err.h"
#include "lwip/sockets.h"
#include "lwip/ip_addr.h"
#include "lwip/inet.h"
#include "mynet.h"  // For net_connect, s_netif
#include "ui.h"     // For setKnobParam, knob params
#include "daisysp.h"

#define SAMPLE_RATE 48000
#define BLOCK_SIZE 96
#define UDP_PORT 5005
#define PACKET_SIZE (BLOCK_SIZE * 3)  // 288 bytes for 96 24-bit samples
#define PRINT_INTERVAL 500  // Print every 500 packets (~1 second)
#define NUM_OSCS 10  // 10 pulse oscillators (harp-inspired "cloud")
#define MAX_TUNE_SPREAD_SEMITONES 2.0f  // Full CW: 2 semitones total spread (±1)
#define CLOUD_GAIN 0.2f  // Full post-mix gain (increased for volume; monitor for clipping)

#define TAG "ASOR"

void sender_task(void* pvParameters);
void receiver_task(void* pvParameters);
// 10 oscillators
daisysp::Oscillator oscs[NUM_OSCS];

// Global params (registered with setKnobParam for UI updates)
volatile float raw_base_freq = 440.0f;  // Raw knob value from KNOB1, DMA-updated
float base_freq = 440.0f;  // KNOB1: Base frequency
volatile float tune_spread = 0.0f;  // KNOB2: Tuning spread (0.0-1.0)
volatile float pw_spread = 0.0f;    // KNOB3: PW spread (0.0-1.0)
//volatile uint8_t knobsUpdated = 0;  // Flag for changes

// Define PACK_L24_BE if not defined elsewhere
#ifndef PACK_L24_BE
#define PACK_L24_BE(p, v) do { \
    (p)[0] = ((v) >> 16) & 0xFF; \
    (p)[1] = ((v) >> 8) & 0xFF;  \
    (p)[2] = (v) & 0xFF;         \
} while (0)
#endif
void exampleButtonCb(uint8_t btn, PressType type) {
    const char* type_str = (type == SHORT_PRESS ? "short" : (type == LONG_PRESS ? "long" : "double"));
    ESP_LOGI(TAG, "Synth: Btn %d %s (e.g., route pot%d to osc freq via patchSave)", btn, type_str, btn);
    // Future: switch(btn) { case 1: if(type==SHORT_PRESS) set_virtual_route(POT_ADC3, OSC_FREQ); }
}

void update_cloud_params() {
    // Limit raw_base_freq to prevent powf overflow (cast volatile to float)
    float safe_raw = std::min(0.4f, (float)raw_base_freq);  // Cap at ~3 octaves
    base_freq = 130.81f * powf(2.0f, safe_raw * 7.0f);  // C3 to ~C9

    // Update oscillators with spread
    for (int i = 0; i < NUM_OSCS; ++i) {
        float detune_ratio = powf(2.0f, ((i - (NUM_OSCS - 1.0f) / 2.0f) / (NUM_OSCS - 1.0f)) * tune_spread * MAX_TUNE_SPREAD_SEMITONES / 12.0f);
        oscs[i].SetFreq(base_freq * detune_ratio);

        float pw = 0.5f + ((i - (NUM_OSCS - 1.0f) / 2.0f) / (NUM_OSCS - 1.0f)) * pw_spread * 0.4f;
        oscs[i].SetPw(pw);
    }

    // Debug: Log knob values every 500 packets
    static int debug_count = 0;
    if (debug_count++ % 500 == 0) {
        printf("Debug: raw_base_freq: %f, tune_spread: %f, pw_spread: %f\n", raw_base_freq, tune_spread, pw_spread);
    }
}

extern "C" void app_main(void) {
    // Initialize NVS
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    // Initialize network stack
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());

    // Connect to network
    ESP_ERROR_CHECK(net_connect());

    // Initialize UI and knobs
    initUI();
    setUILogLevel(ESP_LOG_DEBUG);  // Enable debug for testing
    setButtonCallback(exampleButtonCb);
	
	setKnobParam(KNOB1, &raw_base_freq);  // Raw knob value
	setKnobParam(KNOB2, &tune_spread);    // Tuning spread
	setKnobParam(KNOB3, &pw_spread);      // PW spread
	base_freq = raw_base_freq;  // Sync initial value
	knobsUpdated = 1;  // Force initial update
	
    // Initialize oscillators
	base_freq = 440.0f;  // Start at A4
    for (int i = 0; i < NUM_OSCS; ++i) {
        oscs[i].Init(SAMPLE_RATE);
        oscs[i].SetWaveform(daisysp::Oscillator::WAVE_SQUARE);  // Pulse wave for PW control
        oscs[i].SetAmp(0.3f);  // Full amp (headroom via post-mix)
		oscs[i].SetFreq(base_freq);  // Ensure oscillation
    }

    // Update initial params
    update_cloud_params();

    // Get unicast IP
    esp_netif_ip_info_t ip_info;
    ESP_ERROR_CHECK(esp_netif_get_ip_info(s_netif, &ip_info));
    uint32_t unicast_ip = ip_info.ip.addr;
    printf("Unicast IP: " IPSTR "\n", IP2STR(&ip_info.ip));

    uint8_t* ip_bytes = (uint8_t*)&unicast_ip;
    uint32_t multicast_ip = (239 << 24) | (100 << 16) | (ip_bytes[2] << 8) | ip_bytes[3];
    printf("Computed multicast address: %lu.%lu.%lu.%lu\n",
           (unsigned long)((multicast_ip >> 24) & 0xFF),
           (unsigned long)((multicast_ip >> 16) & 0xFF),
           (unsigned long)((multicast_ip >> 8) & 0xFF),
           (unsigned long)(multicast_ip & 0xFF));

    // Sender task
    xTaskCreate(sender_task, "sender_task", 4096, (void*)&multicast_ip, 5, NULL);

    // Receiver task (stub for now)
    xTaskCreate(receiver_task, "receiver_task", 4096, (void*)&multicast_ip, 5, NULL);

     xTaskCreatePinnedToCore(updateUITask, "updateUI", 2048, NULL, 5, NULL, 1); // Pin to core 1
}

void sender_task(void* pvParameters) {
    uint32_t multicast_ip = *(uint32_t*)pvParameters;

    // Create UDP socket
    int sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (sock < 0) {
        printf("Sender: Socket creation failed: %s (errno %d)\n", strerror(errno), errno);
        vTaskDelete(NULL);
    }
    printf("Sender: Socket created, handle: %d\n", sock);

    // Set TTL for local network
    uint8_t ttl = 1;
    if (setsockopt(sock, IPPROTO_IP, IP_MULTICAST_TTL, &ttl, sizeof(ttl)) < 0) {
        printf("Sender: IP_MULTICAST_TTL failed: %s (errno %d)\n", strerror(errno), errno);
        close(sock);
        vTaskDelete(NULL);
    }

    struct sockaddr_in dest_addr;
    memset(&dest_addr, 0, sizeof(dest_addr));
    dest_addr.sin_family = AF_INET;
    dest_addr.sin_port = htons(UDP_PORT);
    dest_addr.sin_addr.s_addr = htonl(multicast_ip);

    printf("Sender: Starting cloud synth test to %lu.%lu.%lu.%lu:%d\n",
           (unsigned long)((multicast_ip >> 24) & 0xFF),
           (unsigned long)((multicast_ip >> 16) & 0xFF),
           (unsigned long)((multicast_ip >> 8) & 0xFF),
           (unsigned long)(multicast_ip & 0xFF), UDP_PORT);

    TickType_t last_wake_time = xTaskGetTickCount();
    int packet_count = 0;

    while (1) {
        uint8_t buffer[PACKET_SIZE];  // 288 bytes
        int offset = 0;
		if (knobsUpdated) {
		    update_cloud_params();
		    knobsUpdated = 0;
		}
		for (int i = 0; i < BLOCK_SIZE; ++i) {  // 96 samples
		    float mixed_sample = 0.0f;

		    // Mix 10 oscillators (use current params)
		    for (int j = 0; j < NUM_OSCS; ++j) {
		        mixed_sample += oscs[j].Process();
		    }

		    // Clamp with headroom and apply gain
		    mixed_sample = std::max(-1.0f, std::min(1.0f, mixed_sample / NUM_OSCS)) * CLOUD_GAIN;

		    // Debug: Check variation every 500 packets
		    if (i == 0 && packet_count % 500 == 0) {
		        printf("Mixed sample at packet %d: %f, base_freq: %f, tune_spread: %f, pw_spread: %f\n",
		               packet_count, mixed_sample, base_freq, tune_spread, pw_spread);
		    }

		    // Pack 24-bit
		    int32_t value = static_cast<int32_t>(mixed_sample * 8388607.0f);
		    uint8_t tmp[3];
		    PACK_L24_BE(tmp, value);
		    buffer[offset++] = tmp[0];
		    buffer[offset++] = tmp[1];
		    buffer[offset++] = tmp[2];
		}
        int sent = sendto(sock, buffer, PACKET_SIZE, 0, (struct sockaddr*)&dest_addr, sizeof(dest_addr));
        if (sent > 0 && (++packet_count % PRINT_INTERVAL == 0)) {
            printf("Sender: Sent %d bytes (packet #%d)\n", sent, packet_count);
        } else if (sent < 0) {
            printf("Sender: Send failed: %s (errno %d)\n", strerror(errno), errno);
        } else if (sent != PACKET_SIZE) {
            printf("Sender: Sent %d bytes, expected %d\n", sent, PACKET_SIZE);
        }

        vTaskDelayUntil(&last_wake_time, 1);  // 2ms per packet
    }

    close(sock);
    vTaskDelete(NULL);
}

void receiver_task(void* pvParameters) {
    // Stub for future use
    vTaskDelay(pdMS_TO_TICKS(1000));
    vTaskDelete(NULL);
}