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
// Macro to pack a 24-bit signed int into 3 bytes, big-endian (AES67 L24 compliant).
// buffer: uint8_t[3] array to store the bytes.
// value: signed 24-bit int (-8388608 to 8388607).
// Portable: no host endian dependency.
#define PACK_L24_BE(buffer, value) do { \
    uint32_t uval = static_cast<uint32_t>(value); \
    (buffer)[0] = static_cast<uint8_t>((uval >> 16) & 0xFFU); /* MSB */ \
    (buffer)[1] = static_cast<uint8_t>((uval >> 8) & 0xFFU);  /* Middle byte */ \
    (buffer)[2] = static_cast<uint8_t>(uval & 0xFFU);         /* LSB */ \
} while(0)
#define SAMPLE_RATE 48000
#define BLOCK_SIZE 96
#define UDP_PORT 5005
#define UDP_IP "192.168.2.129"  // Replace with audioRecv.py host IP
#define PACKET_SIZE (BLOCK_SIZE * 3)  // 24-bit = 3 bytes per sample
#define PRINT_INTERVAL 500  // Print every 500 packets (~1 second)

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

    osc.Init(SAMPLE_RATE);
    osc.SetWaveform(daisysp::Oscillator::WAVE_SAW);
    osc.SetFreq(440.0f); // A4 note

    // Create UDP socket
    int sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (sock < 0) {
        printf("Socket creation failed\n");
        net_disconnect();
        return;
    }

    struct sockaddr_in dest_addr;
    memset(&dest_addr, 0, sizeof(dest_addr));
    dest_addr.sin_family = AF_INET;
    dest_addr.sin_port = htons(UDP_PORT);
    dest_addr.sin_addr.s_addr = inet_addr(UDP_IP);

    printf("Starting UDP oscillator test on port %d\n", UDP_PORT);

    TickType_t last_wake_time = xTaskGetTickCount();
    int packet_count = 0;

    while (1) {
		uint8_t buffer[PACKET_SIZE];
		int offset = 0;

		for (int i = 0; i < BLOCK_SIZE; ++i) {
		    float sample = osc.Process();
		    // Convert float [-1.0, 1.0] to 24-bit int [-8388608, 8388607]
		    int32_t value = static_cast<int32_t>(sample * 8388607.0f);
		    // Pack using big-endian macro (AES67 L24)
		    uint8_t tmp[3];  // Must be an array (fix for your compile error)
		    PACK_L24_BE(tmp, value);
		    buffer[offset++] = tmp[0];
		    buffer[offset++] = tmp[1];
		    buffer[offset++] = tmp[2];
		}
        // Send buffer via UDP
        int sent = sendto(sock, buffer, PACKET_SIZE, 0,
                          (struct sockaddr*)&dest_addr, sizeof(dest_addr));
        if ((sent >0)&& (++packet_count % PRINT_INTERVAL == 0)){
            printf("Sent %d bytes (packet #%d)\n", sent, packet_count);
        }

// Precise 2ms delay using vTaskDelayUntil
     
        vTaskDelayUntil(&last_wake_time, 1); // configure for 500 Hz timer, so wait for next tick
      //  esp_task_wdt_reset();  // Feed WDT to prevent timeout
    }

    close(sock);
    net_disconnect();
}