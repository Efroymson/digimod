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
#define LEDCOUNT 32  // Total LEDs
#define DUAL_LED_COUNT 8  // First 8 dual-color LEDs
#define SINGLE_LED_COUNT (LEDCOUNT - DUAL_LED_COUNT)  // Last 16 single-color LEDs

// Pin and channel mapping
typedef struct {
    gpio_num_t gpio;
    adc_oneshot_unit_handle_t handle;
    adc_channel_t channel;
} adc_config_t;

static adc_config_t adc_configs[] = {
    {GPIO_NUM_36, NULL, ADC_CHANNEL_0},  // ADC1
    {GPIO_NUM_2,  NULL, ADC_CHANNEL_2},  // ADC3
    {GPIO_NUM_13, NULL, ADC_CHANNEL_3},  // ADC5
    {GPIO_NUM_14, NULL, ADC_CHANNEL_6},  // ADC6
    {GPIO_NUM_4,  NULL, ADC_CHANNEL_4},  // ADC7
    {GPIO_NUM_15, NULL, ADC_CHANNEL_0}   // ADC8
};

#define NUM_ADCS (sizeof(adc_configs) / sizeof(adc_config_t))

// Pin definitions
#define PIN_MOSI GPIO_NUM_32   // 74HC595 MOSI
#define PIN_CLK  GPIO_NUM_16   // 74HC595 CLK
#define PIN_SET_D GPIO_NUM_33  // 74HC595 Latch

// LED state management
volatile StateType LedState[LEDCOUNT] = {RESET};  // State for each LED
volatile bool LedBlinkState[LEDCOUNT] = {false};  // Blink on/off state
uint32_t lastBlinkTime = 0;

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

    // Assign handles to ADC configs
    adc_configs[ADC1].handle = adc1_handle;
    adc_configs[ADC3].handle = adc2_handle;
    adc_configs[ADC5].handle = adc2_handle;
    adc_configs[ADC6].handle = adc2_handle;
    adc_configs[ADC7].handle = adc2_handle;
    adc_configs[ADC8].handle = adc2_handle;

    // Configure ADC channels
    ESP_ERROR_CHECK(adc_oneshot_config_channel(adc_configs[ADC1].handle, adc_configs[ADC1].channel, &chan_cfg));
    ESP_LOGI(TAG, "ADC1 configured on GPIO36 (Pot 1)");
    ESP_ERROR_CHECK(adc_oneshot_config_channel(adc_configs[ADC3].handle, adc_configs[ADC3].channel, &chan_cfg));
    ESP_LOGI(TAG, "ADC3 configured on GPIO2 (Pot 3)");
    ESP_ERROR_CHECK(adc_oneshot_config_channel(adc_configs[ADC5].handle, adc_configs[ADC5].channel, &chan_cfg));
    ESP_LOGI(TAG, "ADC5 configured on GPIO13 (Pot 5)");
    ESP_ERROR_CHECK(adc_oneshot_config_channel(adc_configs[ADC6].handle, adc_configs[ADC6].channel, &chan_cfg));
    ESP_LOGI(TAG, "ADC6 configured on GPIO14 (Pot 6)");
    ESP_ERROR_CHECK(adc_oneshot_config_channel(adc_configs[ADC7].handle, adc_configs[ADC7].channel, &chan_cfg));
    ESP_LOGI(TAG, "ADC7 configured on GPIO4 (Pot 7)");
    ESP_ERROR_CHECK(adc_oneshot_config_channel(adc_configs[ADC8].handle, adc_configs[ADC8].channel, &chan_cfg));
    ESP_LOGI(TAG, "ADC8 configured on GPIO15 (Pot 8)");
}

int readADC(adc_index_t adcNum) {
    if (adcNum >= NUM_ADCS) return -1;

    static int last_values[NUM_ADCS] = {-1};  // Persistent last stable values
    int value;
    esp_err_t ret = adc_oneshot_read(adc_configs[adcNum].handle, adc_configs[adcNum].channel, &value);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "ADC%d read failed: %s", adcNum + 1, esp_err_to_name(ret));
        return -1;
    }
    value = 4095 - value;  // Invert for correct CCW=4095, CW=0
    if (abs(value - last_values[adcNum]) > HYSTERESIS_THRESHOLD || last_values[adcNum] == -1) {
        last_values[adcNum] = value;
        ESP_LOGD(TAG, "ADC%d (GPIO%d) stable: %d", adcNum + 1, adc_configs[adcNum].gpio, last_values[adcNum]);
    }
    return last_values[adcNum];
}

// Function to shift out data to 74HC595 for LEDs
void shiftOutRegister(uint32_t bits_value) {
    gpio_set_level(PIN_SET_D, 0);  // Latch low
    for (uint8_t i = 0; i < LEDCOUNT; i++) {
        bool bitValue = (bits_value >> (LEDCOUNT - 1 - i)) & 0x01;  // MSB to LSB
        gpio_set_level(PIN_MOSI, bitValue);  // Data bit
        gpio_set_level(PIN_CLK, 0);  // Clock low
        gpio_set_level(PIN_CLK, 1);  // Clock high
    }
    gpio_set_level(PIN_SET_D, 1);  // Latch high
    gpio_set_level(PIN_SET_D, 0);  // Latch low (reset)
}

// API to control LED states
void blinkLED(uint8_t ledNum, speed blinkSpeed, colorPattern pattern) {
    if (ledNum >= LEDCOUNT) return;

    LedState[ledNum] = (pattern == red || pattern == green || pattern == yellow) ? SET : BLINK;
    if (ledNum < DUAL_LED_COUNT) {  // Dual-color LEDs (0-7)
        switch (pattern) {
            case redGreenYellow:
                LedBlinkState[ledNum] = (xTaskGetTickCount() % (blinkSpeed == fast ? FAST_BLINK_INTERVAL : SLOW_BLINK_INTERVAL * 2)) < (blinkSpeed == fast ? FAST_BLINK_INTERVAL : SLOW_BLINK_INTERVAL);
                break;
            case redGreen:
                LedBlinkState[ledNum] = (xTaskGetTickCount() % (blinkSpeed == fast ? FAST_BLINK_INTERVAL : SLOW_BLINK_INTERVAL)) < (blinkSpeed == fast ? FAST_BLINK_INTERVAL / 2 : SLOW_BLINK_INTERVAL / 2);
                break;
            case redYellow:
                LedBlinkState[ledNum] = (xTaskGetTickCount() % (blinkSpeed == fast ? FAST_BLINK_INTERVAL : SLOW_BLINK_INTERVAL)) < (blinkSpeed == fast ? FAST_BLINK_INTERVAL / 2 : SLOW_BLINK_INTERVAL / 2);
                break;
            case greenYellow:
                LedBlinkState[ledNum] = (xTaskGetTickCount() % (blinkSpeed == fast ? FAST_BLINK_INTERVAL : SLOW_BLINK_INTERVAL)) < (blinkSpeed == fast ? FAST_BLINK_INTERVAL / 2 : SLOW_BLINK_INTERVAL / 2);
                break;
            case red:
                LedBlinkState[ledNum] = true;  // Red on (bit i)
                break;
            case green:
                LedBlinkState[ledNum] = true;  // Green on (bit i + 8)
                break;
            case yellow:
                LedBlinkState[ledNum] = true;  // Both on
                break;
        }
    } else {  // Single-color LEDs (8-23)
        LedBlinkState[ledNum] = (pattern != red && pattern != green && pattern != yellow) &&
                               (xTaskGetTickCount() % (blinkSpeed == fast ? FAST_BLINK_INTERVAL : SLOW_BLINK_INTERVAL)) < (blinkSpeed == fast ? FAST_BLINK_INTERVAL / 2 : SLOW_BLINK_INTERVAL / 2);
    }
}