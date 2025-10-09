#include <stdio.h>
#include <string.h>
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
#include "mynet.h"
#include "daisysp.h"
#include <stdint.h>
#include "esp_log.h"
#include "lwip/api.h"
#include "ui.h"

#define TAG "OSC"

void sender_task(void* pvParameters);
void receiver_task(void* pvParameters);

#define TNetConn struct netconn *
#define TNetBuf  struct netbuf  *

#ifndef PACK_L24_BE
#define PACK_L24_BE(p, v) do { \
    (p)[0] = ((v) >> 16) & 0xFF; \
    (p)[1] = ((v) >> 8) & 0xFF;  \
    (p)[2] = (v) & 0xFF;         \
} while (0)
#endif

#define SAMPLE_RATE 48000
#define BLOCK_SIZE 96
#define UDP_PORT 5005
#define PACKET_SIZE (BLOCK_SIZE * 3)

#define PRINT_INTERVAL 5000

#define MIN_PW 0.1f  // 10% duty cycle
#define MAX_PW 0.9f  // 90% duty cycle
#define MAX_DETUNE_SEMITONES 2.0f  // ±2 semitones (conservative for beats)
#define MAX_FINE_SEMITONES 12.0f   // Full octave for fine tune (enhanced range)

#define MAX_DETUNE_SEMITONES 2.0f  // ±2 semitones (conservative for beats)
#define MAX_FINE_SEMITONES 12.0f   // Full octave for fine tune (enhanced range)

// Global structure definition for task parameters
struct net_params {
    uint32_t multicast_ip;
    ip_addr_t local_addr;
};

daisysp::Oscillator osc_saw;  // Sawtooth oscillator
daisysp::Oscillator osc_pulse;  // Pulse (variable square) oscillator

// Global shared state for raw knob values (updated by UI task)
volatile float knob_octave = 0.5f;     // KNOB1: Octave
volatile float knob_balance = 0.5f;    // KNOB3: Balance
volatile float knob_fine_tune = 0.5f;  // KNOB5: Fine tune
volatile float knob_pw = 0.5f;         // KNOB7: Pulse width
volatile float knob_detune = 0.5f;     // KNOB8: Detune

// Derived params (computed in sender_task)
float g_freq = 440.0f;
float g_detune_offset = 0.0f;
float g_fine_offset = 0.0f;

// Global for task
button_callback_t g_button_cb = NULL;

void exampleButtonCb(uint8_t btn, PressType type) {
    const char* type_str = (type == SHORT_PRESS ? "short" : (type == LONG_PRESS ? "long" : "double"));
    ESP_LOGI(TAG, "Synth: Btn %d %s (e.g., route pot%d to osc freq via patchSave)", btn, type_str, btn);
    // Future: switch(btn) { case 1: if(type==SHORT_PRESS) set_virtual_route(POT_ADC3, OSC_FREQ); }
}

extern "C" void app_main(void) {
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());

    ESP_ERROR_CHECK(net_connect());

    initUI();
    setUILogLevel(ESP_LOG_DEBUG);  // Enable debug for testing
    setButtonCallback(exampleButtonCb);
    setKnobParam(KNOB1, &knob_octave);     // Register pointers
    setKnobParam(KNOB3, &knob_balance);
    setKnobParam(KNOB5, &knob_fine_tune);
    setKnobParam(KNOB7, &knob_pw);
    setKnobParam(KNOB8, &knob_detune);

    // Initialize oscillators
    osc_saw.Init(SAMPLE_RATE);
    osc_saw.SetWaveform(daisysp::Oscillator::WAVE_SAW);
    osc_pulse.Init(SAMPLE_RATE);
    osc_pulse.SetWaveform(daisysp::Oscillator::WAVE_SQUARE);  // Enables SetPw
    esp_log_level_set("OSC", ESP_LOG_INFO);  // Changed to INFO for debug visibility

    esp_netif_ip_info_t ip_info;
    ESP_ERROR_CHECK(esp_netif_get_ip_info(s_netif, &ip_info));
    uint32_t unicast_ip = ip_info.ip.addr;
    printf("Unicast IP: " IPSTR "\n", IP2STR(&ip_info.ip));

    uint8_t* ip_bytes = (uint8_t*)&unicast_ip;
    uint32_t multicast_ip = (239 << 24) | (100 << 16) | (ip_bytes[2] << 8) | ip_bytes[3];
    ip_addr_t local_addr;
    ip_addr_set_ip4_u32(&local_addr, unicast_ip);
    printf("Computed Multicast: %lu.%lu.%lu.%lu\n",
           (unsigned long)((multicast_ip >> 24) & 0xFF),
           (unsigned long)((multicast_ip >> 16) & 0xFF),
           (unsigned long)((multicast_ip >> 8) & 0xFF),
           (unsigned long)(multicast_ip & 0xFF));

    BaseType_t core_id = 0;

    struct net_params params = {multicast_ip, local_addr};
    if (xTaskCreatePinnedToCore(sender_task, "sender_task", 4096, (void*)&params, 2, NULL, core_id) != pdPASS ||
        xTaskCreatePinnedToCore(receiver_task, "receiver_task", 4096, (void*)&params, 2, NULL, core_id) != pdPASS ||
        xTaskCreatePinnedToCore(updateUITask, "updateUI", 2048, NULL, 5, NULL, 1) != pdPASS) {  // Pin to core 1
        ESP_LOGE(TAG, "Task creation failed - check memory");
    } else {
        ESP_LOGI(TAG, "Tasks created and pinned to core %d", core_id);
    }
}

void sender_task(void* pvParameters) {
    struct net_params *params = (struct net_params *)pvParameters;
    uint32_t multicast_ip = params->multicast_ip;
    ip_addr_t local_addr = params->local_addr;
    struct netconn *conn = netconn_new(NETCONN_UDP);
    if (conn == NULL) {
        printf("Sender: Failed to create netconn: %s (errno %d)\n", strerror(errno), errno);
        vTaskDelete(NULL);
    }
    err_t err = netconn_bind(conn, &local_addr, 0); // Bind to specific Ethernet IP, any port
    if (err != ERR_OK) {
        printf("Sender: Bind failed: %d (local IP: %s)\n", err, ip4addr_ntoa((ip4_addr_t*)&local_addr));
        netconn_delete(conn);
        vTaskDelete(NULL);
    }
    // Attempt to join multicast group with IP_ADDR_ANY as interface
    ip_addr_t any_addr;
    ip_addr_copy(any_addr, *IP_ADDR_ANY); // Initialize any_addr as IP_ADDR_ANY
    ip_addr_t multi_addr;
    ip_addr_set_ip4_u32(&multi_addr, htonl(multicast_ip)); // Ensure network byte order
    printf("Sender: Joining multicast group (raw: 0x%08x, converted: %s, addr: 0x%08x)\n",
           (unsigned int)multicast_ip, ip4addr_ntoa((ip4_addr_t*)&multi_addr), (unsigned int)multi_addr.u_addr.ip4.addr);
    err = netconn_join_leave_group(conn, &multi_addr, &any_addr, NETCONN_JOIN);
    if (err != ERR_OK) {
        printf("Sender: Failed to join multicast group, err: %d\n", err);
    } else {
        printf("Sender: Successfully joined multicast group\n");
    }

    printf("Sender: Starting UDP oscillator test to %lu.%lu.%lu.%lu:%d, bound to %s\n",
           (unsigned long)((multicast_ip >> 24) & 0xFF),
           (unsigned long)((multicast_ip >> 16) & 0xFF),
           (unsigned long)((multicast_ip >> 8) & 0xFF),
           (unsigned long)(multicast_ip & 0xFF), UDP_PORT,
           ip4addr_ntoa((ip4_addr_t*)&local_addr));

    TickType_t last_wake_time = xTaskGetTickCount();
    int packet_count = 0;
    int64_t start_time = esp_timer_get_time();  // For throughput
    int64_t total_bytes = 0;

    // Octave base frequencies (C3 to C8)
    float base_freq[] = {130.81f, 261.63f, 523.25f, 1046.50f, 2093.00f, 4186.01f, 8372.02f, 16744.04f};

    while (1) {
        if (knobsUpdated) {
            // Compute derived params
            int octave_step = (int)(knob_octave * 8.0f);
            octave_step = (octave_step > 7) ? 7 : ((octave_step < 0) ? 0 : octave_step);
            float octave_base = base_freq[octave_step];
            float fine_adj = powf(2.0f, (knob_fine_tune - 0.5f) * MAX_FINE_SEMITONES / 12.0f);  // Enhanced: ±12 semitones as ratio
            g_freq = octave_base * fine_adj;
            g_detune_offset = (knob_detune - 0.5f) * MAX_DETUNE_SEMITONES / 12.0f;  // ±2 semitones as ratio
            osc_saw.SetFreq(g_freq * powf(2.0f, g_detune_offset));
            osc_pulse.SetFreq(g_freq);  // Apply base to pulse
            osc_pulse.SetPw(MIN_PW + knob_pw * (MAX_PW - MIN_PW));
            knobsUpdated = 0;
            ESP_LOGI(TAG, "Sender: Knobs updated, recomputed (freq=%.2f, balance=%.2f, pw=%.2f, detune=%.2f, oct=%.2f, fine=%.2f)",
                     g_freq, knob_balance, knob_pw, g_detune_offset, knob_octave, knob_fine_tune);
        }

        struct netbuf *buf = netbuf_new();
        if (buf == NULL) {
            printf("Sender: netbuf_new failed\n");
            vTaskDelay(1);
            continue;
        }

        uint8_t *data = (uint8_t *)netbuf_alloc(buf, PACKET_SIZE);
        if (data == NULL) {
            printf("Sender: netbuf_alloc failed\n");
            netbuf_delete(buf);
            vTaskDelay(1);
            continue;
        }

        int64_t loop_start = esp_timer_get_time();  // Latency start
        int offset = 0;
        for (int i = 0; i < BLOCK_SIZE; ++i) {
            float saw_sample = osc_saw.Process();
            float pulse_sample = osc_pulse.Process();
            // Mix based on balance
            float sample = (1.0f - knob_balance) * saw_sample + knob_balance * pulse_sample;
            int32_t value = static_cast<int32_t>(sample * 8388607.0f);
            PACK_L24_BE(&data[offset], value);  // Direct packing into netbuf
            offset += 3;
        }

        err_t err = netconn_sendto(conn, buf, &multi_addr, UDP_PORT);  // Send to multicast addr
        int64_t send_end = esp_timer_get_time();  // Latency end

        if (err == ERR_OK) {
            packet_count++;
            total_bytes += PACKET_SIZE;
            if (packet_count % PRINT_INTERVAL == 0) {
                int64_t elapsed_us = send_end - start_time;
                float throughput_kbps = (total_bytes * 8.0f / 1024.0f) / (elapsed_us / 1000000.0f);
                printf("Sender: Sent %d bytes (packet #%d), Throughput=%.2f kbps, Latency=%.2f us\n",
                       PACKET_SIZE, packet_count, throughput_kbps, (float)(send_end - loop_start));
                start_time = esp_timer_get_time();  // Reset
                total_bytes = 0;
            }
        } else {
            printf("Sender: Sendto failed: %d\n", err);
        }

        netbuf_delete(buf);  // Free the netbuf
        vTaskDelayUntil(&last_wake_time, 1);  // 1ms delay
    }

    netconn_delete(conn);
    vTaskDelete(NULL);
}

void receiver_task(void* pvParameters) {
    while (1) vTaskDelay(pdMS_TO_TICKS(20));
}