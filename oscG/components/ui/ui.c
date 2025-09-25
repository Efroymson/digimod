#include <stdio.h>
#include <stdint.h>
#include <math.h>
#include "esp_log.h"
#include "driver/gpio.h"
#include "esp_adc/adc_oneshot.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "ui.h"

#define TAG "UI"
#define HYSTERESIS_THRESHOLD 50  // Adjustable threshold for stability

// Global variables for stable pot values
static int stable_adc1 = -1;  // Pot 1 (GPIO36, octave)
static int stable_adc3 = -1;  // Pot 3 (GPIO2)
static int stable_adc5 = -1;  // Pot 5 (GPIO13, fine tune)
static int stable_adc6 = -1;  // Pot 6 (GPIO14)
static int stable_adc7 = -1;  // Pot 7 (GPIO4)
static int stable_adc8 = -1;  // Pot 8 (GPIO15)

// Pin definitions based on corrected mapping
#define ADC1_GPIO GPIO_NUM_36  // Pot 1, ADC1_CH0
#define ADC3_GPIO GPIO_NUM_2   // Pot 3, ADC2_CH2
#define ADC5_GPIO GPIO_NUM_13  // Pot 5, ADC2_CH3
#define ADC6_GPIO GPIO_NUM_14  // Pot 6, ADC2_CH6
#define ADC7_GPIO GPIO_NUM_4   // Pot 7, ADC2_CH4 (corrected)
#define ADC8_GPIO GPIO_NUM_15  // Pot 8, ADC2_CH0 (corrected)

adc_oneshot_unit_handle_t adc1_handle;
adc_oneshot_unit_handle_t adc2_handle;

void initMinimalADC(void) {
    // Initialize ADC_UNIT_1 for ADC1
    adc_oneshot_unit_init_cfg_t init_cfg1 = {
        .unit_id = ADC_UNIT_1,
        .ulp_mode = ADC_ULP_MODE_DISABLE,
    };
    ESP_ERROR_CHECK(adc_oneshot_new_unit(&init_cfg1, &adc1_handle));
    ESP_LOGI(TAG, "ADC_UNIT_1 initialized");

    // Initialize ADC_UNIT_2 for ADC3, ADC5, ADC6, ADC7, ADC8
    adc_oneshot_unit_init_cfg_t init_cfg2 = {
        .unit_id = ADC_UNIT_2,
        .ulp_mode = ADC_ULP_MODE_DISABLE,
    };
    ESP_ERROR_CHECK(adc_oneshot_new_unit(&init_cfg2, &adc2_handle));
    ESP_LOGI(TAG, "ADC_UNIT_2 initialized");

    adc_oneshot_chan_cfg_t chan_cfg = {
        .atten = ADC_ATTEN_DB_12,  // 0-3.3V range
        .bitwidth = ADC_BITWIDTH_12,  // 12-bit resolution (0-4095)
    };

    // Configure ADC1 (GPIO36 - ADC1_CH0)
    ESP_ERROR_CHECK(adc_oneshot_config_channel(adc1_handle, ADC_CHANNEL_0, &chan_cfg));
    ESP_LOGI(TAG, "ADC1 configured on GPIO36 (Pot 1)");

    // Configure ADC3 (GPIO2 - ADC2_CH2)
    ESP_ERROR_CHECK(adc_oneshot_config_channel(adc2_handle, ADC_CHANNEL_2, &chan_cfg));
    ESP_LOGI(TAG, "ADC3 configured on GPIO2 (Pot 3)");

    // Configure ADC5 (GPIO13 - ADC2_CH3)
    ESP_ERROR_CHECK(adc_oneshot_config_channel(adc2_handle, ADC_CHANNEL_3, &chan_cfg));
    ESP_LOGI(TAG, "ADC5 configured on GPIO13 (Pot 5)");

    // Configure ADC6 (GPIO14 - ADC2_CH6)
    ESP_ERROR_CHECK(adc_oneshot_config_channel(adc2_handle, ADC_CHANNEL_6, &chan_cfg));
    ESP_LOGI(TAG, "ADC6 configured on GPIO14 (Pot 6)");

    // Configure ADC7 (GPIO4 - ADC2_CH4)
    ESP_ERROR_CHECK(adc_oneshot_config_channel(adc2_handle, ADC_CHANNEL_4, &chan_cfg));
    ESP_LOGI(TAG, "ADC7 configured on GPIO4 (Pot 7)");

    // Configure ADC8 (GPIO15 - ADC2_CH0)
    ESP_ERROR_CHECK(adc_oneshot_config_channel(adc2_handle, ADC_CHANNEL_0, &chan_cfg));
    ESP_LOGI(TAG, "ADC8 configured on GPIO15 (Pot 8)");
}

int readADC1(void) {  // Pot 1: Octave control
    static int last_value = -1;
    int value;
    esp_err_t ret = adc_oneshot_read(adc1_handle, ADC_CHANNEL_0, &value);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "ADC1 read failed: %s", esp_err_to_name(ret));
        return -1;
    }
    value = 4095 - value;  // Invert for correct CCW=4095, CW=0
    if (abs(value - last_value) > HYSTERESIS_THRESHOLD || last_value == -1) {
        last_value = value;
        ESP_LOGD(TAG, "ADC1 (Pot 1, GPIO36) stable: %d", last_value);
    }
    return last_value;
}

int readADC3(void) {  // Pot 3
    static int last_value = -1;
    int value;
    esp_err_t ret = adc_oneshot_read(adc2_handle, ADC_CHANNEL_2, &value);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "ADC3 read failed: %s", esp_err_to_name(ret));
        return -1;
    }
    value = 4095 - value;  // Invert
    if (abs(value - last_value) > HYSTERESIS_THRESHOLD || last_value == -1) {
        last_value = value;
        ESP_LOGD(TAG, "ADC3 (Pot 3, GPIO2) stable: %d", last_value);
    }
    return last_value;
}

int readADC5(void) {  // Pot 5: Fine tune control
    static int last_value = -1;
    int value;
    esp_err_t ret = adc_oneshot_read(adc2_handle, ADC_CHANNEL_3, &value);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "ADC5 read failed: %s", esp_err_to_name(ret));
        return -1;
    }
    value = 4095 - value;  // Invert
    if (abs(value - last_value) > HYSTERESIS_THRESHOLD || last_value == -1) {
        last_value = value;
        ESP_LOGD(TAG, "ADC5 (Pot 5, GPIO13) stable: %d", last_value);
    }
    return last_value;
}

int readADC6(void) {  // Pot 6
    static int last_value = -1;
    int value;
    esp_err_t ret = adc_oneshot_read(adc2_handle, ADC_CHANNEL_6, &value);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "ADC6 read failed: %s", esp_err_to_name(ret));
        return -1;
    }
    value = 4095 - value;  // Invert
    if (abs(value - last_value) > HYSTERESIS_THRESHOLD || last_value == -1) {
        last_value = value;
        ESP_LOGD(TAG, "ADC6 (Pot 6, GPIO14) stable: %d", last_value);
    }
    return last_value;
}

int readADC7(void) {  // Pot 7
    static int last_value = -1;
    int value;
    esp_err_t ret = adc_oneshot_read(adc2_handle, ADC_CHANNEL_4, &value);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "ADC7 read failed: %s", esp_err_to_name(ret));
        return -1;
    }
    value = 4095 - value;  // Invert
    if (abs(value - last_value) > HYSTERESIS_THRESHOLD || last_value == -1) {
        last_value = value;
        ESP_LOGD(TAG, "ADC7 (Pot 7, GPIO4) stable: %d", last_value);
    }
    return last_value;
}

int readADC8(void) {  // Pot 8
    static int last_value = -1;
    int value;
    esp_err_t ret = adc_oneshot_read(adc2_handle, ADC_CHANNEL_0, &value);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "ADC8 read failed: %s", esp_err_to_name(ret));
        return -1;
    }
    value = 4095 - value;  // Invert
    if (abs(value - last_value) > HYSTERESIS_THRESHOLD || last_value == -1) {
        last_value = value;
        ESP_LOGD(TAG, "ADC8 (Pot 8, GPIO15) stable: %d", last_value);
    }
    return last_value;
}