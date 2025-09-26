#include <stdio.h>
#include <stdint.h>
#include "esp_log.h"
#include "driver/gpio.h"
#include "esp_adc/adc_oneshot.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "ui.h"

#define TAG "UI"
#define LEDCOUNT 32
#define DUAL_LED_COUNT 8

#define PIN_MOSI GPIO_NUM_32
#define PIN_CLK  GPIO_NUM_16
#define PIN_SET_D GPIO_NUM_33

volatile StateType LedState[LEDCOUNT] = {RESET};
volatile bool LedBlinkState[LEDCOUNT] = {false};
volatile uint32_t LedBlinkCount[LEDCOUNT] = {0};  // Initialize to 0 (no blink)
uint32_t lastBlinkTime = 0;

typedef struct {
    gpio_num_t gpio;
    adc_oneshot_unit_handle_t handle;
    adc_channel_t channel;
} adc_config_t;

static adc_config_t adc_configs[] = {
    {GPIO_NUM_36, NULL, ADC_CHANNEL_0},
    {GPIO_NUM_2,  NULL, ADC_CHANNEL_2},
    {GPIO_NUM_13, NULL, ADC_CHANNEL_3},
    {GPIO_NUM_14, NULL, ADC_CHANNEL_6},
    {GPIO_NUM_4,  NULL, ADC_CHANNEL_4},
    {GPIO_NUM_15, NULL, ADC_CHANNEL_0}
};

#define NUM_ADCS (sizeof(adc_configs) / sizeof(adc_config_t))

static adc_oneshot_unit_handle_t adc1_handle;
static adc_oneshot_unit_handle_t adc2_handle;

void initUI(void) {
    gpio_config_t io_conf = {
        .intr_type = GPIO_INTR_DISABLE,
        .mode = GPIO_MODE_OUTPUT,
        .pin_bit_mask = (1ULL << PIN_MOSI) | (1ULL << PIN_CLK) | (1ULL << PIN_SET_D),
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .pull_up_en = GPIO_PULLUP_DISABLE
    };
    ESP_ERROR_CHECK(gpio_config(&io_conf));
    ESP_LOGI(TAG, "GPIO configured");

    for (int i = 0; i < LEDCOUNT; i++) {
        LedState[i] = RESET;
        LedBlinkState[i] = false;
        LedBlinkCount[i] = 0;
    }
    shiftOutRegister(0);
    ESP_LOGI(TAG, "LEDs reset to off");

    adc_oneshot_unit_init_cfg_t init_cfg1 = {.unit_id = ADC_UNIT_1, .ulp_mode = ADC_ULP_MODE_DISABLE};
    ESP_ERROR_CHECK(adc_oneshot_new_unit(&init_cfg1, &adc1_handle));

    adc_oneshot_unit_init_cfg_t init_cfg2 = {.unit_id = ADC_UNIT_2, .ulp_mode = ADC_ULP_MODE_DISABLE};
    ESP_ERROR_CHECK(adc_oneshot_new_unit(&init_cfg2, &adc2_handle));

    adc_oneshot_chan_cfg_t chan_cfg = {.atten = ADC_ATTEN_DB_12, .bitwidth = ADC_BITWIDTH_12};

    adc_configs[ADC1].handle = adc1_handle;
    for (int i = ADC3; i <= ADC8; i++) adc_configs[i].handle = adc2_handle;

    for (int i = 0; i < NUM_ADCS; i++) {
        ESP_ERROR_CHECK(adc_oneshot_config_channel(adc_configs[i].handle, adc_configs[i].channel, &chan_cfg));
        ESP_LOGI(TAG, "ADC%d configured", i+1);
    }
}

int readADC(adc_index_t adcNum) {
    if (adcNum >= NUM_ADCS) return -1;

    static int last_values[NUM_ADCS] = {-1};
    int value;
    esp_err_t ret = adc_oneshot_read(adc_configs[adcNum].handle, adc_configs[adcNum].channel, &value);
    if (ret != ESP_OK) return -1;
    value = 4095 - value;
    if (abs(value - last_values[adcNum]) > HYSTERESIS_THRESHOLD || last_values[adcNum] == -1) {
        last_values[adcNum] = value;
    }
    return last_values[adcNum];
}

void shiftOutRegister(uint32_t bits_value) {
    gpio_set_level(PIN_SET_D, 0);
    for (uint8_t i = 0; i < LEDCOUNT; i++) {
        bool bitValue = (bits_value >> (LEDCOUNT - 1 - i)) & 0x01;  // Reverse order
        gpio_set_level(PIN_MOSI, !bitValue);  // Inverted for common anode
        gpio_set_level(PIN_CLK, 0);
        gpio_set_level(PIN_CLK, 1);
    }
    gpio_set_level(PIN_SET_D, 1);
    gpio_set_level(PIN_SET_D, 0);
}

void setLedBitState(uint8_t bitNum, StateType state) {
    if (bitNum >= LEDCOUNT) return;
    LedState[bitNum] = state;
    LedBlinkCount[bitNum] = 0;  // Disable blinking
    LedBlinkState[bitNum] = (state == SET);
}

void blinkLedBit(uint8_t bitNum, speed blinkSpeed) {
    if (bitNum >= LEDCOUNT) return;
    LedState[bitNum] = SET;
    LedBlinkCount[bitNum] = (blinkSpeed == fast) ? (FAST_BLINK_INTERVAL_MS / UI_UPDATE_INTERVAL_MS) : (SLOW_BLINK_INTERVAL_MS / UI_UPDATE_INTERVAL_MS);  // Set initial count
    LedBlinkState[bitNum] = true;
}

void blinkLED(uint8_t ledNum, speed blinkSpeed, colorPattern pattern) {
    if (ledNum < DUAL_LED_COUNT) {
        uint8_t red_bit = ledNum;
        uint8_t green_bit = ledNum + DUAL_LED_COUNT;
        switch (pattern) {
            case green:
                setLedBitState(red_bit, RESET);
                setLedBitState(green_bit, SET);
                break;
            case red:
                setLedBitState(red_bit, SET);
                setLedBitState(green_bit, RESET);
                break;
            case yellow:
                setLedBitState(red_bit, SET);
                setLedBitState(green_bit, SET);
                break;
            case redGreen:
                blinkLedBit(red_bit, blinkSpeed);
                blinkLedBit(green_bit, blinkSpeed);
                LedBlinkState[green_bit] = false;  // Start off
                break;
            case redGreenYellow:
                blinkLedBit(red_bit, blinkSpeed);
                blinkLedBit(green_bit, blinkSpeed);
                break;
            case redYellow:
                setLedBitState(red_bit, SET);
                blinkLedBit(green_bit, blinkSpeed);
                break;
            case greenYellow:
                blinkLedBit(red_bit, blinkSpeed);
                setLedBitState(green_bit, SET);
                break;
            default:
                ESP_LOGE(TAG, "Unsupported pattern %d for LED %d", pattern, ledNum);
                break;
        }
    } else if (ledNum < (DUAL_LED_COUNT + SINGLE_LED_COUNT)) {
        uint8_t bit = ledNum + DUAL_LED_COUNT;
        if (pattern == red || pattern == green || pattern == yellow) {
            setLedBitState(bit, SET);
        } else {
            blinkLedBit(bit, blinkSpeed);
        }
    } else {
        ESP_LOGE(TAG, "Invalid LED: %d", ledNum);
    }
}

void updateUITask(void *pvParameters) {
    ESP_LOGI(TAG, "UI task started on core %d", xPortGetCoreID());
    TickType_t last_wake = xTaskGetTickCount();
    uint32_t tick_counter = 0;
    uint32_t last_led_bits = 0;
    while (1) {
        uint32_t led_bits = 0;
        bool any_change = false;
        for (uint8_t i = 0; i < LEDCOUNT; i++) {
            if (LedBlinkCount[i] > 0) {
                LedBlinkCount[i]--;
                if (LedBlinkCount[i] == 0) {
                    LedBlinkState[i] = !LedBlinkState[i];
                    LedState[i] = LedBlinkState[i] ? SET : RESET;
                    any_change = true;
                    ESP_LOGI(TAG, "Toggled bit %d to %d", i, LedBlinkState[i]);
                    LedBlinkCount[i] = (LedBlinkCount[i] == 0) ? ((LedBlinkState[i]) ? (FAST_BLINK_INTERVAL_MS / UI_UPDATE_INTERVAL_MS) : (SLOW_BLINK_INTERVAL_MS / UI_UPDATE_INTERVAL_MS)) : LedBlinkCount[i];  // Reset based on initial speed
                }
            }
            if (LedState[i] == SET) {
                led_bits |= (1U << i);
            }
        }
        if (led_bits != last_led_bits) {
            ESP_LOGI(TAG, "Shifting bits: 0x%08x", led_bits);
            shiftOutRegister(led_bits);
            last_led_bits = led_bits;
        }

        tick_counter++;
        vTaskDelayUntil(&last_wake, pdMS_TO_TICKS(10));
    }
}