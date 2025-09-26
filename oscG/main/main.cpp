// main.cpp
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
#define PRINT_INTERVAL 500

daisysp::Oscillator osc;

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

    osc.Init(SAMPLE_RATE);
    osc.SetWaveform(daisysp::Oscillator::WAVE_SIN);

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
	        xTaskCreatePinnedToCore(updateUITask, "updateUI", 4096, NULL, 3, &dummy_handle, core_id) != pdPASS) {
	        ESP_LOGE(TAG, "Task creation failed - check memory");
	    } else {
	        ESP_LOGI(TAG, "Tasks created and pinned to core %d", core_id);
	    }

	    // Full blinking test for all 32 LEDs
	    for (int i = 0; i < DUAL_LED_COUNT; i++) {
	        blinkLED(i, slow, redGreen);  // Alternating red/green for dual LEDs 0-7
	    }
	    for (int i = DUAL_LED_COUNT; i < (DUAL_LED_COUNT + SINGLE_LED_COUNT); i++) {  // Include 23 (bit 31)
	        blinkLED(i, fast, redGreen);  // Alternating blink for single LEDs 8-23
	    }
	    ESP_LOGI(TAG, "LED test set - expecting alternating red/green blink on 0-23, off on 24-31 if wired");

	    esp_log_level_set("UI", ESP_LOG_INFO);
	    esp_log_level_set(TAG, ESP_LOG_INFO);
	}

	void updateOscTask(void *pvParameters) {
	    ESP_LOGI(TAG, "OSC task started on core %d", xPortGetCoreID());
	    static int last_adc1 = -1, last_adc5 = -1;
	    while (1) {
	        int adc1_val = readADC(ADC1);
	        int adc5_val = readADC(ADC5);
	        if (adc1_val != -1 && adc5_val != -1) {
	            int octave_step = adc1_val / 512;
	            octave_step = (octave_step > 7) ? 7 : octave_step;
	            float base_freq[] = {130.81f, 261.63f, 523.25f, 1046.50f, 2093.00f, 4186.01f, 8372.02f, 16744.04f};
	            float octave_base = base_freq[octave_step];
	            float fineAdj = 1.0f + (float)adc5_val / 4095.0f;
	            float final_freq = octave_base * fineAdj;
	            osc.SetFreq(final_freq);
	            if (abs(adc1_val - last_adc1) > HYSTERESIS_THRESHOLD || abs(adc5_val - last_adc5) > HYSTERESIS_THRESHOLD) {
	                ESP_LOGI(TAG, "Freq updated: %.2f Hz (ADC1=%d, ADC5=%d)", final_freq, adc1_val, adc5_val);
	                last_adc1 = adc1_val;
	                last_adc5 = adc5_val;
	            }
	        }
	        vTaskDelay(pdMS_TO_TICKS(10));  // Restore to 10ms for smooth tracking
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
            float sample = osc.Process();
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