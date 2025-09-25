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
#define LEDCOUNT 32  // Assuming 32 LEDs across 74HC595 chips

// Global variables for stable pot values
static int stable_adc1 = -1;  // Pot 1 (GPIO36, octave)
static int stable_adc3 = -1;  // Pot 3 (GPIO2)
static int stable_adc5 = -1;  // Pot 5 (GPIO13, fine tune)
static int stable_adc6 = -1;  // Pot 6 (GPIO14)
static int stable_adc7 = -1;  // Pot 7 (GPIO4)
static int stable_adc8 = -1;  // Pot 8 (GPIO15)

// Pin definitions
#define ADC1_GPIO GPIO_NUM_36  // Pot 1, ADC1_CH0
#define ADC3_GPIO GPIO_NUM_2   // Pot 3, ADC2_CH2
#define ADC5_GPIO GPIO_NUM_13  // Pot 5, ADC2_CH3
#define ADC6_GPIO GPIO_NUM_14  // Pot 6, ADC2_CH6
#define ADC7_GPIO GPIO_NUM_4   // Pot 7, ADC2_CH4
#define ADC8_GPIO GPIO_NUM_15  // Pot 8, ADC2_CH0
#define PIN_MOSI GPIO_NUM_32   // 74HC595 MOSI
#define PIN_CLK  GPIO_NUM_16   // 74HC595 CLK
#define PIN_SET_D GPIO_NUM_33  // 74HC595 Latch

volatile bool LedStatus[LEDCOUNT] = {false};  // LED states
volatile StateType LedState[LEDCOUNT] = {RESET};  // Initial state

adc_oneshot_unit_handle_t adc1_handle;
adc_oneshot_unit_handle_t adc2_handle;

void initUI(void) {
    // Configure GPIO for 74HC595
    gpio_config_t io_conf = {
        .intr_type = GPIO_INTR_DISABLE,
        .mode = GPIO_MODE_OUTPUT,
        .pin_bit_mask = (1ULL << PIN_MOSI) | (1ULL << PIN_CLK) | (1ULL << PIN_SET_D),
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .pull_up_en = GPIO_PULLUP_DISABLE
    };
    ESP_ERROR_CHECK(gpio_config(&io_conf));
    ESP_LOGI(TAG, "GPIO configured for 74HC595 (MOSI:32, CLK:16, SET_D:33)");

    // Initial LED state (all off)
    shiftOutRegister(0);

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

    // Configure ADC channels
    ESP_ERROR_CHECK(adc_oneshot_config_channel(adc1_handle, ADC_CHANNEL_0, &chan_cfg));
    ESP_LOGI(TAG, "ADC1 configured on GPIO36 (Pot 1)");
    ESP_ERROR_CHECK(adc_oneshot_config_channel(adc2_handle, ADC_CHANNEL_2, &chan_cfg));
    ESP_LOGI(TAG, "ADC3 configured on GPIO2 (Pot 3)");
    ESP_ERROR_CHECK(adc_oneshot_config_channel(adc2_handle, ADC_CHANNEL_3, &chan_cfg));
    ESP_LOGI(TAG, "ADC5 configured on GPIO13 (Pot 5)");
    ESP_ERROR_CHECK(adc_oneshot_config_channel(adc2_handle, ADC_CHANNEL_6, &chan_cfg));
    ESP_LOGI(TAG, "ADC6 configured on GPIO14 (Pot 6)");
    ESP_ERROR_CHECK(adc_oneshot_config_channel(adc2_handle, ADC_CHANNEL_4, &chan_cfg));
    ESP_LOGI(TAG, "ADC7 configured on GPIO4 (Pot 7)");
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
    value = 4095 - value;  // Invert
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

// Function to shift out data to 74HC595 for LEDs
void shiftOutRegister(uint32_t bits_value) {
    gpio_set_level(PIN_SET_D, 0);  // Latch low
    for (uint8_t i = 0; i < LEDCOUNT; i++) {
        bool bitValue = (bits_value >> (LEDCOUNT - 1 - i)) & 0x01;  // Shift from MSB to LSB
        gpio_set_level(PIN_MOSI, bitValue);  // Data bit
        gpio_set_level(PIN_CLK, 0);  // Clock low
        gpio_set_level(PIN_CLK, 1);  // Clock high
    }
    gpio_set_level(PIN_SET_D, 1);  // Latch high
    gpio_set_level(PIN_SET_D, 0);  // Latch low (optional reset)
}