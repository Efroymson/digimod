// main.cpp
#include <stdio.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_system.h"
#include "esp_netif.h"
#include "esp_event.h"
#include "lwip/err.h"
#include "lwip/sockets.h"
#include "lwip/ip_addr.h"
#include "lwip/inet.h"
#include "mynet.h"
#include <stdint.h>
#include "esp_log.h"
#include "ui.h"

#define TAG "HWTEST"



void hwTestTask(void* pvParameters);

#define PRINT_INTERVAL 5000
#define HYSTERESIS_THRESHOLD 50  // From ui.h, for ADC stability

// Global for task
button_callback_t g_button_cb = NULL;

void exampleButtonCb(uint8_t btn, PressType type) {
    const char* type_str = (type == SHORT_PRESS ? "short" : (type == LONG_PRESS ? "long" : "double"));
    ESP_LOGI(TAG, "Synth: Btn %d %s (e.g., route pot%d to osc freq via patchSave)", btn, type_str, btn);
    // Future: switch(btn) { case 1: if(type==SHORT_PRESS) set_virtual_route(POT_ADC3, OSC_FREQ); }
}

extern "C" void app_main(void) {
    esp_netif_ip_info_t ip_info;

    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());

    ESP_ERROR_CHECK(net_connect());

    initUI();
    setUILogLevel(ESP_LOG_WARN);
    //shiftOutRegister(ox0);
    setButtonCallback(exampleButtonCb);
    testUI();  // Activates blinks

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

       if ( xTaskCreatePinnedToCore(hwTestTask, "hwTest", 4096, NULL, 3, &dummy_handle, core_id) != pdPASS ||
        xTaskCreatePinnedToCore(updateUITask, "updateUI", 2048, NULL, 5, NULL, 1) != pdPASS) {  // Pin to core 1
        ESP_LOGE(TAG, "Task creation failed - check memory");
    } else {
        ESP_LOGI(TAG, "Tasks created and pinned to core %d", core_id);
    }
}

void hwTestTask(void * pvParameters) {
    ESP_LOGI(TAG, "hwTes task started on core %d", xPortGetCoreID());
    TickType_t last_adc_log_time = xTaskGetTickCount();

    while (1) {
       float adc1_val = readKnob(KNOB1); // Octave (GPIO36)
       float adc2_val = readKnob(KNOB2); // Balance (GPIO35, corrected)
       float adc3_val = readKnob(KNOB3); // Fine tune (GPIO13)
       float adc4_val = readKnob(KNOB4); // Pulse width (GPIO4)
       float adc5_val = readKnob(KNOB5); // Detune (GPIO15)
       float adc6_val = readKnob(KNOB6);
	   float adc7_val = readKnob(KNOB7);
	   float adc8_val = readKnob(KNOB8);

        // Diagnostic: Log normalized ADC values periodically
        if (xTaskGetTickCount() - last_adc_log_time >= pdMS_TO_TICKS(2000)) {
            ESP_LOGI(TAG, "ADC raw values: ADC1=%.2f, ADC2=%.2f, ADC3=%.2f,  ADC4=%.2f,  ADC5=%.2f, ADC6=%.2f,  ADC7=%.2f,  ADC8=%.2f ", 
                     adc1_val, adc2_val, adc3_val, adc4_val, adc5_val, adc6_val, adc7_val, adc8_val); /*, adc7_val, adc8_val)*/
            last_adc_log_time = xTaskGetTickCount();
        }

        vTaskDelay(pdMS_TO_TICKS(10));  // 10ms for smooth tracking
    }
}

