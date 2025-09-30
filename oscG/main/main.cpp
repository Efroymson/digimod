// main.cpp
#include <stdio.h>
#include <string.h>
#include <cmath>  // For powf in detune calculation
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
#include "ui.h"

#define TAG "OSC"

void sender_task(void* pvParameters);
void receiver_task(void* pvParameters);
void updateOscTask(void* pvParameters);

#ifndef PACK_L24_BE
#define PACK_L24_BE(p, v) do { (p)[0] = ((v) >> 16) & 0xFF; (p)[1] = ((v) >> 8) & 0xFF; (p)[2] = (v) & 0xFF; } while (0)
#endif

#define SAMPLE_RATE 48000
#define BLOCK_SIZE 96
#define UDP_PORT 5005
#define PACKET_SIZE (BLOCK_SIZE * 3)
#define PRINT_INTERVAL 5000
#define HYSTERESIS_THRESHOLD 50  // From ui.h, for ADC stability
#define MIN_PW 0.1f  // 10% duty cycle
#define MAX_PW 0.9f  // 90% duty cycle
#define MAX_DETUNE_SEMITONES 2.0f  // ±2 semitones
#define ADC_LOG_INTERVAL_MS 500  // Diagnostic: Log raw ADC values every 500ms

daisysp::Oscillator osc_saw;  // Sawtooth oscillator
daisysp::Oscillator osc_pulse;  // Pulse (variable square) oscillator

// Global shared state for balance (volatile for thread-safety in multi-task environment)
volatile float g_balance = 0.5f;  // Default to centered mix (50/50 saw/pulse)

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
    setUILogLevel(ESP_LOG_INFO);
    //shiftOutRegister(ox0);
    setButtonCallback(exampleButtonCb);
    //testUI();  // Activates blinks

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
    printf("Multicast: %lu.%lu.%lu.%lu\n",
           (unsigned long)((multicast_ip >> 24) & 0xFF),
           (unsigned long)((multicast_ip >> 16) & 0xFF),
           (unsigned long)((multicast_ip >> 8) & 0xFF),
           (unsigned long)(multicast_ip & 0xFF));

    BaseType_t core_id = 0;
    TaskHandle_t dummy_handle;

    if (xTaskCreatePinnedToCore(sender_task, "sender_task", 4096, (void*)&multicast_ip, 2, NULL, core_id) != pdPASS ||
        xTaskCreatePinnedToCore(receiver_task, "receiver_task", 4096, (void*)&multicast_ip, 2, NULL, core_id) != pdPASS ||
        xTaskCreatePinnedToCore(updateOscTask, "updateOsc", 4096, NULL, 3, &dummy_handle, core_id) != pdPASS ||
        xTaskCreatePinnedToCore(updateUITask, "updateUI", 2048, NULL, 5, NULL, 1) != pdPASS) {  // Pin to core 1
        ESP_LOGE(TAG, "Task creation failed - check memory");
    } else {
        ESP_LOGI(TAG, "Tasks created and pinned to core %d", core_id);
    }
}

void updateOscTask(void *pvParameters) {
    ESP_LOGI(TAG, "OSC task started on core %d", xPortGetCoreID());
    static int last_adc1 = -1, last_adc6 = -1, last_adc5 = -1, last_adc7 = -1, last_adc8 = -1;  // Changed ADC3 to ADC6
    TickType_t last_adc_log_time = xTaskGetTickCount();

    while (1) {
        int adc1_val = readADC(ADC1);  // Octave
        int adc6_val = readADC(ADC6);  // Balance (replaced ADC3)
        int adc5_val = readADC(ADC5);  // Fine tune
        int adc7_val = readADC(ADC7);  // Pulse width
        int adc8_val = readADC(ADC8);  // Detune

        // Diagnostic: Log raw ADC values periodically to check hardware response
        if (xTaskGetTickCount() - last_adc_log_time >= pdMS_TO_TICKS(ADC_LOG_INTERVAL_MS)) {
            ESP_LOGI(TAG, "ADC raw values: ADC1=%d, ADC6=%d, ADC5=%d, ADC7=%d, ADC8=%d",
                     adc1_val, adc6_val, adc5_val, adc7_val, adc8_val);
            last_adc_log_time = xTaskGetTickCount();
        }

        // Apply hysteresis and update only if changed significantly
        bool update_needed = false;
        if (abs(adc1_val - last_adc1) > HYSTERESIS_THRESHOLD) { last_adc1 = adc1_val; update_needed = true; }
        if (abs(adc6_val - last_adc6) > HYSTERESIS_THRESHOLD) { last_adc6 = adc6_val; update_needed = true; }  // Changed ADC3 to ADC6
        if (abs(adc5_val - last_adc5) > HYSTERESIS_THRESHOLD) { last_adc5 = adc5_val; update_needed = true; }
        if (abs(adc7_val - last_adc7) > HYSTERESIS_THRESHOLD) { last_adc7 = adc7_val; update_needed = true; }
        if (abs(adc8_val - last_adc8) > HYSTERESIS_THRESHOLD) { last_adc8 = adc8_val; update_needed = true; }

        if (update_needed && adc1_val != -1 && adc6_val != -1 && adc5_val != -1 && adc7_val != -1 && adc8_val != -1) {
            // Octave and fine tune (base for both oscillators)
            int octave_step = adc1_val / 512;
            octave_step = (octave_step > 7) ? 7 : ((octave_step < 0) ? 0 : octave_step);  // Added bounds
            float base_freq[] = {130.81f, 261.63f, 523.25f, 1046.50f, 2093.00f, 4186.01f, 8372.02f, 16744.04f};
            float octave_base = base_freq[octave_step];
            float fine_adj = 1.0f + (float)adc5_val / 4095.0f;
            float base_freq_val = octave_base * fine_adj;

            // Balance (0.0 = all saw, 1.0 = all pulse) - update global, now from ADC6
            float balance = (float)adc6_val / 4095.0f;
            g_balance = balance;

            // Pulse width (0.1 to 0.9)
            float pw = MIN_PW + ((float)adc7_val / 4095.0f) * (MAX_PW - MIN_PW);
            osc_pulse.SetPw(pw);

            // Detune (±2 semitones)
            float detune_semi = ((float)adc8_val / 4095.0f - 0.5f) * (2.0f * MAX_DETUNE_SEMITONES);
            float detune_mult = powf(2.0f, detune_semi / 12.0f);
            float freq_pulse = base_freq_val * detune_mult;

            // Set frequencies
            osc_saw.SetFreq(base_freq_val);
            osc_pulse.SetFreq(freq_pulse);

            ESP_LOGI(TAG, "Osc updated: Freq=%.2f Hz (saw), %.2f Hz (pulse), Bal=%.2f, PW=%.2f, Det=%.2f semi (ADCs:1=%d,6=%d,5=%d,7=%d,8=%d)",
                     base_freq_val, freq_pulse, g_balance, pw, detune_semi, adc1_val, adc6_val, adc5_val, adc7_val, adc8_val);
        }
        vTaskDelay(pdMS_TO_TICKS(10));  // 10ms for smooth tracking
    }
}

void sender_task(void* pvParameters) {
    uint32_t multicast_ip = *(uint32_t*)pvParameters;
    int sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (sock < 0) {
        printf("Sender: Socket creation failed: %s (errno %d)\n", strerror(errno), errno);
        vTaskDelete(NULL);
    }
    printf("Sender: Socket created, handle: %d\n", sock);

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

    printf("Sender: Starting UDP oscillator test to %lu.%lu.%lu.%lu:%d\n",
           (unsigned long)((multicast_ip >> 24) & 0xFF),
           (unsigned long)((multicast_ip >> 16) & 0xFF),
           (unsigned long)((multicast_ip >> 8) & 0xFF),
           (unsigned long)(multicast_ip & 0xFF), UDP_PORT);

    TickType_t last_wake_time = xTaskGetTickCount();
    int packet_count = 0;

    while (1) {
        uint8_t buffer[PACKET_SIZE];
        int offset = 0;
        for (int i = 0; i < BLOCK_SIZE; ++i) {
            float saw_sample = osc_saw.Process();
            float pulse_sample = osc_pulse.Process();
            // Mix based on shared global balance (updated by updateOscTask)
            float sample = (1.0f - g_balance) * saw_sample + g_balance * pulse_sample;
            int32_t value = static_cast<int32_t>(sample * 8388607.0f);  // 24-bit range
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
        vTaskDelayUntil(&last_wake_time, 1);  // 1ms delay
    }
    close(sock);
    vTaskDelete(NULL);
}

void receiver_task(void* pvParameters) {
    uint32_t multicast_ip = *(uint32_t*)pvParameters;
    int sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (sock < 0) {
        printf("Receiver: Socket creation failed: %s (errno %d)\n", strerror(errno), errno);
        vTaskDelete(NULL);
    }
    printf("Receiver: Socket created, handle: %d\n", sock);

    int on = 1;
    if (setsockopt(sock, SOL_SOCKET, SO_REUSEADDR, &on, sizeof(on)) < 0) {
        printf("Receiver: SO_REUSEADDR failed: %s (errno %d)\n", strerror(errno), errno);
        close(sock);
        vTaskDelete(NULL);
    }

    struct sockaddr_in bind_addr;
    memset(&bind_addr, 0, sizeof(bind_addr));
    bind_addr.sin_family = AF_INET;
    bind_addr.sin_port = htons(UDP_PORT);
    bind_addr.sin_addr.s_addr = htonl(INADDR_ANY);
    if (bind(sock, (struct sockaddr*)&bind_addr, sizeof(bind_addr)) < 0) {
        printf("Receiver: Bind failed: %s (errno %d)\n", strerror(errno), errno);
        close(sock);
        vTaskDelete(NULL);
    }

    struct ip_mreq mreq;
    mreq.imr_multiaddr.s_addr = htonl(multicast_ip);
    mreq.imr_interface.s_addr = htonl(INADDR_ANY);
    if (setsockopt(sock, IPPROTO_IP, IP_ADD_MEMBERSHIP, &mreq, sizeof(mreq)) < 0) {
        printf("Receiver: IP_ADD_MEMBERSHIP failed: %s (errno %d)\n", strerror(errno), errno);
        close(sock);
        vTaskDelete(NULL);
    } else {
        printf("Receiver: Joined multicast group %lu.%lu.%lu.%lu\n",
               (unsigned long)((multicast_ip >> 24) & 0xFF),
               (unsigned long)((multicast_ip >> 16) & 0xFF),
               (unsigned long)((multicast_ip >> 8) & 0xFF),
               (unsigned long)(multicast_ip & 0xFF));
    }

    uint8_t buffer[PACKET_SIZE];
    struct sockaddr_in source_addr;
    socklen_t addr_len = sizeof(source_addr);

    while (1) {
        int len = recvfrom(sock, buffer, PACKET_SIZE, 0, (struct sockaddr*)&source_addr, &addr_len);
        if (len > 0) {
            char ip_str[16];
            inet_ntop(AF_INET, &source_addr.sin_addr, ip_str, sizeof(ip_str));
            printf("Receiver: Received %d bytes from %s:%d\n", len, ip_str, ntohs(source_addr.sin_port));
        } else if (len < 0) {
            printf("Receiver: Recv failed: %s (errno %d)\n", strerror(errno), errno);
        }
        vTaskDelay(1 / portTICK_PERIOD_MS);
    }
    close(sock);
    vTaskDelete(NULL);
}