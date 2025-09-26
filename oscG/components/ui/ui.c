#include <stdio.h>
#include <stdint.h>
#include <string.h>  // For memset
#include "esp_log.h"
#include "driver/gpio.h"
#include "esp_adc/adc_oneshot.h"
#include "esp_timer.h"  // For button timing
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "ui.h"

#define PIN_MOSI GPIO_NUM_32
#define PIN_CLK  GPIO_NUM_16
#define PIN_SET_D GPIO_NUM_33
#define PIN_SHLD GPIO_NUM_3  // Schematic: Output for 74HC165 PL (latch/load)
#define PIN_QH   GPIO_NUM_5  // Schematic: Input for Q7 (serial out, high=pressed)

static const char *TAG = "UI";

volatile StateType LedState[LEDCOUNT] = {RESET};
volatile bool LedBlinkState[LEDCOUNT] = {false};
volatile uint32_t LedBlinkCount[LEDCOUNT] = {0};
volatile speed LedBlinkSpeed[LEDCOUNT] = {slow};
uint32_t lastBlinkTime = 0;

// Chetu-style button globals
static bool buttonCurrentStatus[BUTTONSCOUNT] = {false};
static bool buttonLastStatus[BUTTONSCOUNT] = {false};
static uint16_t prev_button_state = 0;  // For reg change log
static button_callback_t g_button_cb = NULL;  // Global cb

typedef struct {
    gpio_num_t gpio;
    adc_oneshot_unit_handle_t handle;
    adc_channel_t channel;
} adc_config_t;

static adc_config_t adc_configs[] = {
    {GPIO_NUM_36, NULL, ADC_CHANNEL_0},  // ADC1 idx0
    {GPIO_NUM_2,  NULL, ADC_CHANNEL_2},  // ADC3 idx1
    {GPIO_NUM_13, NULL, ADC_CHANNEL_3},  // ADC5 idx2
    {GPIO_NUM_14, NULL, ADC_CHANNEL_6},  // ADC6 idx3
    {GPIO_NUM_4,  NULL, ADC_CHANNEL_4},  // ADC7 idx4
    {GPIO_NUM_15, NULL, ADC_CHANNEL_0}   // ADC8 idx5 ADC2 CH0
};

#define NUM_ADCS (sizeof(adc_configs) / sizeof(adc_config_t))

static adc_oneshot_unit_handle_t adc1_handle;
static adc_oneshot_unit_handle_t adc2_handle;

/**
 * @brief Exact chetu readShiftRegister (high QH = pressed = set bit).
 * @return State mask (LSB = btn1).
 */
static uint16_t readButtonRegister(void) {
    gpio_set_level(PIN_SHLD, 0);
    gpio_set_level(PIN_CLK, 0);
    gpio_set_level(PIN_CLK, 1);
    gpio_set_level(PIN_SHLD, 1);
    uint16_t switch_value = 0;
    for (int i = 0; i < BUTTONSCOUNT; i++) {
        if (gpio_get_level(PIN_QH)) {
            switch_value |= (1 << i);
        }
        gpio_set_level(PIN_CLK, 0);
        gpio_set_level(PIN_CLK, 1);
    }
    return switch_value;
}

static void pollButtons(void) {
    if (!g_button_cb) {
        ESP_LOGW(TAG, "No button cb set");  // One-time
        return;
    }

    static bool first_poll = true;
    static bool is_long_press[BUTTONSCOUNT] = {false};
    static uint64_t press_start_time[BUTTONSCOUNT] = {0};

    uint16_t registerValue = readButtonRegister();
    uint64_t current_time = esp_timer_get_time();

    if (first_poll || registerValue != prev_button_state) {
        ESP_LOGI(TAG, "Button reg: 0x%04x", registerValue);
        first_poll = false;
    }

    for (int i = 0; i < BUTTONSCOUNT; i++) {
        buttonCurrentStatus[i] = (registerValue >> i) & 0x01;

        // Press edge
        if (buttonCurrentStatus[i] && !buttonLastStatus[i]) {
            press_start_time[i] = current_time;
            is_long_press[i] = false;
        }

        // Held (continuous press)
        if (buttonCurrentStatus[i] && buttonLastStatus[i]) {
            if (!is_long_press[i] &&
                (current_time - press_start_time[i] > LONG_PRESS_THRESHOLD_US)) {
                is_long_press[i] = true;  // Mark but don't trigger yet
            }
        }

        // Release edge
        if (!buttonCurrentStatus[i] && buttonLastStatus[i]) {
            uint64_t duration = current_time - press_start_time[i];
            if (is_long_press[i] || duration > LONG_PRESS_THRESHOLD_US) {
                g_button_cb((uint8_t)(i + 1), LONG_PRESS);
            } else {
                g_button_cb((uint8_t)(i + 1), SHORT_PRESS);
            }
        }

        buttonLastStatus[i] = buttonCurrentStatus[i];
    }
    prev_button_state = registerValue;
}

void setUILogLevel(esp_log_level_t level) {
    esp_log_level_set(TAG, level);
}

static void initButtons(void) {
    // Separate configs: SHLD output, QH input
    gpio_config_t shld_conf = {
        .intr_type = GPIO_INTR_DISABLE,
        .mode = GPIO_MODE_OUTPUT,
        .pin_bit_mask = (1ULL << PIN_SHLD),
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .pull_up_en = GPIO_PULLUP_DISABLE
    };
    ESP_ERROR_CHECK(gpio_config(&shld_conf));

    gpio_config_t qh_conf = {
        .intr_type = GPIO_INTR_DISABLE,
        .mode = GPIO_MODE_INPUT,
        .pin_bit_mask = (1ULL << PIN_QH),
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .pull_up_en = GPIO_PULLUP_DISABLE  // External per schematic
    };
    ESP_ERROR_CHECK(gpio_config(&qh_conf));

    // Init SHLD high (idle)
    gpio_set_level(PIN_SHLD, 1);
    ESP_LOGI(TAG, "Button GPIOs configured (SHLD out, QH in)");
}

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

    // Reset LEDs
    memset((void*)LedState, RESET, sizeof(LedState));
    memset((void*)LedBlinkState, 0, sizeof(LedBlinkState));
    memset((void*)LedBlinkCount, 0, sizeof(LedBlinkCount));
    memset((void*)LedBlinkSpeed, slow, sizeof(LedBlinkSpeed));
    shiftOutRegister(0);
    ESP_LOGI(TAG, "LEDs reset to off");

    adc_oneshot_unit_init_cfg_t init_cfg1 = {.unit_id = ADC_UNIT_1, .ulp_mode = ADC_ULP_MODE_DISABLE};
    ESP_ERROR_CHECK(adc_oneshot_new_unit(&init_cfg1, &adc1_handle));

    adc_oneshot_unit_init_cfg_t init_cfg2 = {.unit_id = ADC_UNIT_2, .ulp_mode = ADC_ULP_MODE_DISABLE};
    ESP_ERROR_CHECK(adc_oneshot_new_unit(&init_cfg2, &adc2_handle));

    adc_oneshot_chan_cfg_t chan_cfg = {.atten = ADC_ATTEN_DB_12, .bitwidth = ADC_BITWIDTH_12};

    adc_configs[ADC1].handle = adc1_handle;
    adc_configs[ADC3].handle = adc1_handle;
    for (int i = ADC5; i <= ADC8; i++) {
        adc_configs[i].handle = adc2_handle;
    }

    for (int i = 0; i < NUM_ADCS; i++) {
        ESP_ERROR_CHECK(adc_oneshot_config_channel(adc_configs[i].handle, adc_configs[i].channel, &chan_cfg));
        ESP_LOGI(TAG, "ADC%d configured", i + 1);
    }

    initButtons();

    // Baseline button read (post-init, pre-task)
    uint16_t baseline = readButtonRegister();
    ESP_LOGI(TAG, "Button baseline reg: 0x%04x", baseline);
}

int readADC(adc_index_t adcNum) {
    if (adcNum >= NUM_ADCS) return -1;

    static int last_values[NUM_ADCS] = {-1};
    int value;
    esp_err_t ret = adc_oneshot_read(adc_configs[adcNum].handle, adc_configs[adcNum].channel, &value);
    if (ret != ESP_OK) return -1;
    value = 4095 - value;  // Hardware invert
    if (abs(value - last_values[adcNum]) > HYSTERESIS_THRESHOLD || last_values[adcNum] == -1) {
        last_values[adcNum] = value;
    }
    return last_values[adcNum];
}

void shiftOutRegister(uint32_t bits_value) {
    gpio_set_level(PIN_SET_D, 0);
    for (uint8_t i = 0; i < LEDCOUNT; i++) {
        bool bitValue = (bits_value >> (LEDCOUNT - 1 - i)) & 0x01;  // Reverse for MSB first
        gpio_set_level(PIN_MOSI, !bitValue);  // Invert common anode
        gpio_set_level(PIN_CLK, 0);
        gpio_set_level(PIN_CLK, 1);
    }
    gpio_set_level(PIN_SET_D, 1);
    gpio_set_level(PIN_SET_D, 0);
}

void setLedBitState(uint8_t bitNum, StateType state) {
    if (bitNum >= LEDCOUNT) {
        ESP_LOGE(TAG, "Invalid bit %d", bitNum);
        return;
    }
    LedState[bitNum] = state;
    LedBlinkCount[bitNum] = 0;
    LedBlinkState[bitNum] = (state == SET);
    LedBlinkSpeed[bitNum] = slow;
}

void blinkLedBit(uint8_t bitNum, speed blinkSpeed) {
    if (bitNum >= LEDCOUNT) {
        ESP_LOGE(TAG, "Invalid bit %d", bitNum);
        return;
    }
    LedBlinkSpeed[bitNum] = blinkSpeed;
    LedBlinkState[bitNum] = true;  // Start ON
    LedState[bitNum] = SET;
    uint32_t interval = (blinkSpeed == fast) ? FAST_BLINK_INTERVAL_MS : SLOW_BLINK_INTERVAL_MS;
    LedBlinkCount[bitNum] = interval / UI_UPDATE_INTERVAL_MS;
}

void blinkLED(uint8_t ledNum, speed blinkSpeed, colorPattern pattern) {
    if (ledNum >= (DUAL_LED_COUNT + SINGLE_LED_COUNT)) {
        ESP_LOGE(TAG, "Invalid LED %d", ledNum);
        return;
    }
    if (ledNum < DUAL_LED_COUNT) {
        uint8_t red_bit = ledNum;       // 0-7
        uint8_t green_bit = ledNum + DUAL_LED_COUNT;  // 8-15
        switch (pattern) {
            case green:
                setLedBitState(red_bit, RESET);
                setLedBitState(green_bit, SET);
                return;
            case red:
                setLedBitState(red_bit, SET);
                setLedBitState(green_bit, RESET);
                return;
            case yellow:
                setLedBitState(red_bit, SET);
                setLedBitState(green_bit, SET);
                return;
            case redGreen:
                blinkLedBit(red_bit, blinkSpeed);  // Start red ON
                LedBlinkState[green_bit] = false;
                LedState[green_bit] = RESET;
                LedBlinkSpeed[green_bit] = blinkSpeed;
                LedBlinkCount[green_bit] = (blinkSpeed == fast ? FAST_BLINK_INTERVAL_MS : SLOW_BLINK_INTERVAL_MS) / UI_UPDATE_INTERVAL_MS;
                return;
            case redGreenYellow:
                blinkLedBit(red_bit, blinkSpeed);
                blinkLedBit(green_bit, blinkSpeed);
                return;
            case redYellow:
                setLedBitState(green_bit, SET);
                blinkLedBit(red_bit, blinkSpeed);
                return;
            case greenYellow:
                setLedBitState(green_bit, SET);
                LedBlinkState[red_bit] = false;
                LedState[red_bit] = RESET;
                LedBlinkSpeed[red_bit] = blinkSpeed;
                LedBlinkCount[red_bit] = (blinkSpeed == fast ? FAST_BLINK_INTERVAL_MS : SLOW_BLINK_INTERVAL_MS) / UI_UPDATE_INTERVAL_MS;
                return;
            default:
                ESP_LOGE(TAG, "Unsupported pattern %d", pattern);
                return;
        }
    } else {
        // Singles: ledNum 8-23 -> bits 16-31
        uint8_t bit = ledNum + 8;
        if (bit >= LEDCOUNT) {
            ESP_LOGE(TAG, "Single LED %d overflow bit %d", ledNum, bit);
            return;
        }
        if (pattern == red || pattern == green || pattern == yellow) {
            setLedBitState(bit, SET);
        } else {
            blinkLedBit(bit, blinkSpeed);
        }
    }
}

void setButtonCallback(button_callback_t cb) {
    g_button_cb = cb;
    ESP_LOGI(TAG, "Button cb set");
}

void testUI(void) {
    // Dual slow redGreen
    for (uint8_t i = 0; i < DUAL_LED_COUNT; i++) {
        blinkLED(i, slow, redGreen);
    }
    // Singles fast blink
    for (uint8_t i = DUAL_LED_COUNT; i < (DUAL_LED_COUNT + SINGLE_LED_COUNT); i++) {
        blinkLedBit(i + 8, fast);
    }
    ESP_LOGI(TAG, "LED test activated");
}

void updateUITask(void *pvParameters) {
    ESP_LOGI(TAG, "UI task started on core %d", xPortGetCoreID());
    TickType_t last_wake = xTaskGetTickCount();
    uint32_t last_led_bits = 0;

    while (1) {
        // Poll buttons FIRST (minimize CLK overlap with LED shift)
        pollButtons();

        uint32_t led_bits = 0;
        for (uint8_t i = 0; i < LEDCOUNT; i++) {
            if (LedBlinkCount[i] > 0) {
                LedBlinkCount[i]--;
                if (LedBlinkCount[i] == 0) {
                    LedBlinkState[i] = !LedBlinkState[i];
                    LedState[i] = LedBlinkState[i] ? SET : RESET;

                    uint32_t interval = (LedBlinkSpeed[i] == fast) ? FAST_BLINK_INTERVAL_MS : SLOW_BLINK_INTERVAL_MS;
                    LedBlinkCount[i] = interval / UI_UPDATE_INTERVAL_MS;
                }
            }
            if (LedState[i] == SET) {
                led_bits |= (1U << i);
            }
        }
        if (led_bits != last_led_bits) {
            shiftOutRegister(led_bits);
            last_led_bits = led_bits;
        }

        vTaskDelayUntil(&last_wake, pdMS_TO_TICKS(UI_UPDATE_INTERVAL_MS));
    }
}