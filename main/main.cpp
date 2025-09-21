#include <stdio.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_eth.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "nvs_flash.h"
#include "driver/adc.h"
#include "driver/gpio.h"
#include "lwip/sockets.h"
#include "lwip/udp.h"
#include "sdkconfig.h"  // For Kconfig defines

#include "pot_controller.h"  // Reused from your sketch
#include "oscillator.h"     // New: Audio generation

#define TAG "VCO_Module"
#define POT_FREQ    ((adc2_channel_t)0)  // Knob 1: Frequency (0-1 -> 20-2000 Hz)
#define POT_SHAPE   ((adc2_channel_t)1)  // Knob 2: Shape (0=sine, 1=square)
#define BUTTON      ((gpio_num_t)0)      // For future: e.g., toggle mode
#define MULTICAST_IP "239.255.255.250"
#define MULTICAST_PORT 5004
#define SAMPLE_RATE 48000
#define BLOCK_SIZE 96  // 2 ms at 48 kHz
#define AUDIO_BUFFER_SIZE (BLOCK_SIZE * 10)  // Jitter buffer depth

static PotController freq_pot;
static PotController shape_pot;
static Oscillator osc;  // Audio generator

static int udp_sock = -1;
static struct sockaddr_in dest_addr;

// Reused/adapted from your button_task (debounced press for future use)
static void button_task(void *pvParameters) {
    gpio_set_direction(BUTTON, GPIO_MODE_INPUT);
    gpio_pullup_en(BUTTON);
    ESP_LOGI(TAG, "Button task started");
    for (;;) {
        while (gpio_get_level(BUTTON)) vTaskDelay(pdMS_TO_TICKS(50));
        vTaskDelay(pdMS_TO_TICKS(25));  // Debounce
        if (!gpio_get_level(BUTTON)) {
            ESP_LOGI(TAG, "Button pressed (future: toggle mode)");
            while (!gpio_get_level(BUTTON)) vTaskDelay(pdMS_TO_TICKS(10));
        }
    }
}

// Adapted from your pot_task; now two pots
static void control_task(void *pvParameters) {
    TickType_t xLastWakeTime = xTaskGetTickCount();
    const TickType_t period = pdMS_TO_TICKS(24);  // Control rate
    int val;

    // Init pots: Freq 0-1 -> 20-2000 Hz (scale/offset)
    freq_pot.Init(NULL, NULL, 1980.0f, 20.0f);  // Main param unused here
    shape_pot.Init(NULL, NULL, 1.0f, 0.0f);

    adc2_config_channel_atten(POT_FREQ, ADC_ATTEN_DB_11);
    adc2_config_channel_atten(POT_SHAPE, ADC_ATTEN_DB_11);

    for (;;) {
        // Read pots
        adc2_get_raw(POT_FREQ, ADC_WIDTH_BIT_12, &val);
        freq_pot.ProcessControlRate(val / 4095.0f);
        adc2_get_raw(POT_SHAPE, ADC_WIDTH_BIT_12, &val);
        shape_pot.ProcessControlRate(val / 4095.0f);

        // Update osc params (UI rate not needed for pots here)
        osc.SetFrequency(freq_pot.value_);
        osc.SetShape(shape_pot.value_);

        vTaskDelayUntil(&xLastWakeTime, period);
    }
}

// New: DSP task - generate + send RTP-like packets
static void dsp_task(void *pvParameters) {
    int16_t audio_buffer[AUDIO_BUFFER_SIZE];  // Mono 24-bit in 32-bit, but pack as 16 for sim
    uint8_t packet[1536];  // MTU-safe
    struct udp_pcb *pcb = udp_new();  // lwIP UDP
    if (!pcb) {
        ESP_LOGE(TAG, "Failed to create UDP PCB");
        vTaskDelete(NULL);
    }

    // Setup multicast
    ip_addr_t dest_ip;
    ipaddr_aton(MULTICAST_IP, &dest_ip);
    dest_addr.sin_addr.s_addr = dest_ip.u_addr.ip4.addr;
    dest_addr.sin_family = AF_INET;
    dest_addr.sin_port = htons(MULTICAST_PORT);
    udp_connect(pcb, (ip_addr_t*)&dest_ip, MULTICAST_PORT);

    TickType_t xLastWakeTime = xTaskGetTickCount();
    const TickType_t period = pdMS_TO_TICKS(2);
    uint32_t rtp_seq = 0;
    uint32_t rtp_ts = 0;
    int bytes_sent;

    ESP_LOGI(TAG, "DSP task started: Sending to %s:%d", MULTICAST_IP, MULTICAST_PORT);

    for (;;) {
        // Generate block
        osc.Render(audio_buffer, BLOCK_SIZE);

        // Pack simple RTP header (12 bytes) + payload (24-bit samples as 32-bit)
        // RTP: V=2, P=0, X=0, CC=0, M=0, PT=96 (dynamic), Seq, TS, SSRC=0
        packet[0] = 0x80;  // V=2, P=0, X=0, CC=0
        packet[1] = 0x60;  // M=0, PT=96
        *(uint16_t*)(packet + 2) = htons(rtp_seq++);  // Seq
        *(uint32_t*)(packet + 4) = htonl(rtp_ts);  // TS (increment by BLOCK_SIZE)
        rtp_ts += BLOCK_SIZE;
        *(uint32_t*)(packet + 8) = 0;  // SSRC

        // Payload: 96 * 3 bytes (24-bit)
        for (int i = 0; i < BLOCK_SIZE; ++i) {
            // Pack 24-bit to 3 bytes (big-endian)
            uint32_t sample = (uint32_t)audio_buffer[i] & 0xFFFFFF;  // Assume signed 24-bit
            packet[12 + i*3 + 0] = (sample >> 16) & 0xFF;
            packet[12 + i*3 + 1] = (sample >> 8) & 0xFF;
            packet[12 + i*3 + 2] = sample & 0xFF;
        }
        int packet_len = 12 + BLOCK_SIZE * 3;

        // Send via lwIP
        bytes_sent = udp_send(pcb, (const void*)packet, packet_len);
        if (bytes_sent != packet_len) {
            ESP_LOGE(TAG, "Send failed: %d bytes (expected %d)", bytes_sent, packet_len);
        }

        vTaskDelayUntil(&xLastWakeTime, period);
    }
    udp_remove(pcb);
    vTaskDelete(NULL);
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
    ESP_ERROR_CHECK(esp_eth_init(esp_netif_new(ESP_NETIF_DEFAULT_ETH())));  // Ethernet init

    // Oscillator init
    osc.Init(SAMPLE_RATE);

    // Create tasks (pinned to core 0 for determinism)
    xTaskCreatePinnedToCore(dsp_task, "dsp", 4096, NULL, 5, NULL, 0);
    xTaskCreatePinnedToCore(control_task, "control", 2048, NULL, 2, NULL, 0);
    xTaskCreatePinnedToCore(button_task, "button", 2048, NULL, 1, NULL, 0);
}