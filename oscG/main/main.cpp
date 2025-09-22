#include <stdio.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_system.h"
#include "esp_netif.h"
#include "esp_event.h"
#include "nvs_flash.h"
#include "lwip/sockets.h"
#include "lwip/inet.h"
#include "mynet.h"
#include "daisysp.h"

#define SAMPLE_RATE 48000
#define BLOCK_SIZE 96
#define UDP_PORT 5005
#define UDP_IP "192.168.1.100"  // Replace with mcu.py host IP

static daisysp::Oscillator osc;

extern "C" void app_main(void) {
    // Initialize NVS (Non-Volatile Storage)
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    // Initialize network stack
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());

    // Connect to network (from your net.h/net.c)
    ESP_ERROR_CHECK(net_connect());

    // Initialize oscillator
    osc.Init(SAMPLE_RATE);
    osc.SetWaveform(daisysp::Oscillator::WAVE_SAW);
    osc.SetFreq(440.0f); // A4 note

    // Create UDP socket
    int sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (sock < 0) {
        printf("Socket creation failed\n");
        vTaskDelete(NULL);
    }

    struct sockaddr_in dest_addr;
    memset(&dest_addr, 0, sizeof(dest_addr));
    dest_addr.sin_family = AF_INET;
    dest_addr.sin_port = htons(UDP_PORT);
    dest_addr.sin_addr.s_addr = inet_addr(UDP_IP); // Target IP for mcu.py

    printf("Starting UDP oscillator test on port %d\n", UDP_PORT);
    int packet_count = 0;
    
	while (1) {
        float buffer[BLOCK_SIZE];
        for (int i = 0; i < BLOCK_SIZE; ++i) {
            buffer[i] = osc.Process();
        }
        // Send buffer via UDP
        int sent = sendto(sock, buffer, BLOCK_SIZE * sizeof(float), 0,
                          (struct sockaddr*)&dest_addr, sizeof(dest_addr));
        if (sent > 0) packet_count++;
		if (packet_count % 500 == 0) {
		            printf("Sent %d packets\n", packet_count);
		        }
        vTaskDelay(1 / portTICK_PERIOD_MS); // Avoid watchdog
    }

    close(sock); // Unreachable but good practice
}
