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

	    // Connect to network (blocks until IP is assigned)
	    ESP_ERROR_CHECK(net_connect());

	    osc.Init(SAMPLE_RATE);
	    osc.SetWaveform(daisysp::Oscillator::WAVE_SAW);
	    osc.SetFreq(440.0f); // A4 note

	    // Get unicast IP from netif
	    esp_netif_ip_info_t ip_info;
	    ESP_ERROR_CHECK(esp_netif_get_ip_info(s_netif, &ip_info));
	    uint32_t unicast_ip = ip_info.ip.addr;
	    printf("Unicast IP: " IPSTR "\n", IP2STR(&ip_info.ip));  // lwIP-safe print

	    uint8_t* ip_bytes = (uint8_t*)&unicast_ip;
	    uint32_t multicast_ip = (239 << 24) | (100 << 16) | (ip_bytes[2] << 8) | ip_bytes[3];
	    printf("Computed multicast address: %lu.%lu.%lu.%lu\n",
	           (unsigned long)((multicast_ip >> 24) & 0xFF),
	           (unsigned long)((multicast_ip >> 16) & 0xFF),
	           (unsigned long)((multicast_ip >> 8) & 0xFF),
	           (unsigned long)(multicast_ip & 0xFF));

	    // Create UDP socket
	    int sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
	    if (sock < 0) {
	        printf("Socket creation failed: %s (errno %d)\n", strerror(errno), errno);
	        net_disconnect();
	        return;
	    }
	    printf("Socket created, handle: %d\n", sock);

	    // Set TTL for local network (as in main.cpp.chetu)
	    uint8_t ttl = 1;
	    if (setsockopt(sock, IPPROTO_IP, IP_MULTICAST_TTL, &ttl, sizeof(ttl)) < 0) {
	        printf("IP_MULTICAST_TTL failed: %s (errno %d)\n", strerror(errno), errno);
	        close(sock);
	        net_disconnect();
	        return;
	    }

	    // Configure destination address (no join needed for sender)
	    struct sockaddr_in dest_addr;
	    memset(&dest_addr, 0, sizeof(dest_addr));
	    dest_addr.sin_family = AF_INET;
	    dest_addr.sin_port = htons(UDP_PORT);  // Assuming UDP_PORT is 5004 or similar
	    dest_addr.sin_addr.s_addr = htonl(multicast_ip);  // Network byte order

	    printf("Starting UDP oscillator test on port %d to multicast %lu.%lu.%lu.%lu\n",
	           UDP_PORT,
	           (unsigned long)((multicast_ip >> 24) & 0xFF),
	           (unsigned long)((multicast_ip >> 16) & 0xFF),
	           (unsigned long)((multicast_ip >> 8) & 0xFF),
	           (unsigned long)(multicast_ip & 0xFF));

	    // Sending loop (unchanged structure, enhanced logging)
	    TickType_t last_wake_time = xTaskGetTickCount();
	    int packet_count = 0;

	    while (1) {
	        uint8_t buffer[PACKET_SIZE];
	        int offset = 0;

	        for (int i = 0; i < BLOCK_SIZE; ++i) {
	            float sample = osc.Process();
	            int32_t value = static_cast<int32_t>(sample * 8388607.0f);
	            uint8_t tmp[3];
	            PACK_L24_BE(tmp, value);
	            buffer[offset++] = tmp[0];
	            buffer[offset++] = tmp[1];
	            buffer[offset++] = tmp[2];
	        }

	        int sent = sendto(sock, buffer, PACKET_SIZE, 0, (struct sockaddr*)&dest_addr, sizeof(dest_addr));
	        if (sent > 0 && (++packet_count % PRINT_INTERVAL == 0)) {
	            printf("Sent %d bytes (packet #%d)\n", sent, packet_count);
	        } else if (sent < 0) {
	            printf("Send failed: %s (errno %d)\n", strerror(errno), errno);
	        }

	        vTaskDelayUntil(&last_wake_time, 1);
	    }

	    close(sock);
	    net_disconnect();
	}