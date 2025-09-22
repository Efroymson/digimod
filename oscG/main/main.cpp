#include <stdio.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_system.h"
#include "esp_netif.h"
#include "esp_event.h"         // For esp_event_loop_create_default
#include "nvs_flash.h"         // For nvs_flash_init, nvs_flash_erase
#include "lwip/err.h"
#include "lwip/sockets.h"
#include "lwip/ip_addr.h"
#include "lwip/inet.h"
#include "mynet.h"
#include "daisysp.h"           // Assuming oscillator includes

// Forward declarations
void sender_task(void* pvParameters);
void receiver_task(void* pvParameters);

// Define PACK_L24_BE if not in a header
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
#define PACKET_SIZE (BLOCK_SIZE * 3)  // 288 bytes for 96 24-bit samples
#define PRINT_INTERVAL 500  // Print every 500 packets (~1 second)

daisysp::Oscillator osc;

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

    // Initialize oscillator
    osc.Init(SAMPLE_RATE);
    osc.SetWaveform(daisysp::Oscillator::WAVE_SAW);
    osc.SetFreq(440.0f);

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

    // Receiver task
    xTaskCreate(receiver_task, "receiver_task", 4096, (void*)&multicast_ip, 5, NULL);

    while (1) {
        vTaskDelay(1000 / portTICK_PERIOD_MS);  // Keep main alive
    }
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

    printf("Sender: Starting UDP oscillator test to %lu.%lu.%lu.%lu:%d\n",
           (unsigned long)((multicast_ip >> 24) & 0xFF),
           (unsigned long)((multicast_ip >> 16) & 0xFF),
           (unsigned long)((multicast_ip >> 8) & 0xFF),
           (unsigned long)(multicast_ip & 0xFF), UDP_PORT);

    TickType_t last_wake_time = xTaskGetTickCount();
    int packet_count = 0;

    while (1) {
        uint8_t buffer[PACKET_SIZE];  // 288 bytes
        int offset = 0;

        for (int i = 0; i < BLOCK_SIZE; ++i) {  // 96 samples
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

        vTaskDelayUntil(&last_wake_time, 1);  // 2ms per packet
    }

    close(sock);
    vTaskDelete(NULL);
}

void receiver_task(void* pvParameters) {
    uint32_t multicast_ip = *(uint32_t*)pvParameters;

    // Create UDP socket
    int sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (sock < 0) {
        printf("Receiver: Socket creation failed: %s (errno %d)\n", strerror(errno), errno);
        vTaskDelete(NULL);
    }
    printf("Receiver: Socket created, handle: %d\n", sock);

    // Enable reuse address (for bind)
    int on = 1;
    if (setsockopt(sock, SOL_SOCKET, SO_REUSEADDR, &on, sizeof(on)) < 0) {
        printf("Receiver: SO_REUSEADDR failed: %s (errno %d)\n", strerror(errno), errno);
        close(sock);
        vTaskDelete(NULL);
    }

    // Bind to multicast port
    struct sockaddr_in bind_addr;
    memset(&bind_addr, 0, sizeof(bind_addr));
    bind_addr.sin_family = AF_INET;
    bind_addr.sin_port = htons(UDP_PORT);
    bind_addr.sin_addr.s_addr = htonl(INADDR_ANY);  // Bind to all interfaces
    if (bind(sock, (struct sockaddr*)&bind_addr, sizeof(bind_addr)) < 0) {
        printf("Receiver: Bind failed: %s (errno %d)\n", strerror(errno), errno);
        close(sock);
        vTaskDelete(NULL);
    }

    // Join multicast group
    struct ip_mreq mreq;
    mreq.imr_multiaddr.s_addr = htonl(multicast_ip);
    mreq.imr_interface.s_addr = htonl(INADDR_ANY);  // Let lwIP choose interface
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

    // Receive loop
    uint8_t buffer[PACKET_SIZE];  // 288 bytes
    struct sockaddr_in source_addr;
    socklen_t addr_len = sizeof(source_addr);

    while (1) {
        int len = recvfrom(sock, buffer, PACKET_SIZE, 0, (struct sockaddr*)&source_addr, &addr_len);
        if (len > 0) {
            char ip_str[16];
            inet_ntop(AF_INET, &source_addr.sin_addr, ip_str, sizeof(ip_str));
            printf("Receiver: Received %d bytes from %s:%d\n", len, ip_str, ntohs(source_addr.sin_port));
            // Process buffer (e.g., unpack 96 samples) if needed
        } else if (len < 0) {
            printf("Receiver: Recv failed: %s (errno %d)\n", strerror(errno), errno);
        }
        vTaskDelay(1 / portTICK_PERIOD_MS);  // Prevent tight loop
    }

    close(sock);
    vTaskDelete(NULL);
}