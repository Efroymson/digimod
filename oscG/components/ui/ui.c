#include <stdio.h>
#include <stdint.h>
#include <math.h>  // For fabs
#include <stdbool.h>  // For bool
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

// Button globals (simplified, no double-click)
static bool buttonCurrentStatus[BUTTONSCOUNT] = {false};
static bool buttonLastStatus[BUTTONSCOUNT] = {false};
static uint64_t timerStart[BUTTONSCOUNT] = {0};  // Used for long press start time
static uint64_t pressDuration[BUTTONSCOUNT] = {0};  // Used for long press duration
static bool longPressDetected[BUTTONSCOUNT] = {false};  // Flag for long press
static uint16_t prev_button_state = 0;  // For reg change log
static button_callback_t g_button_cb = NULL;  // Global cb

// Knob chasing globals
static float saved_knob_values[NUM_KNOBS] = {0.5f, 0.5f, 0.5f, 0.5f, 0.5f, 0.5f};  // Default mid, normalized
static bool is_chasing[NUM_KNOBS] = {false};

volatile StateType LedState[LEDCOUNT] = {RESET};
volatile bool LedBlinkState[LEDCOUNT] = {false};
volatile uint32_t LedBlinkCount[LEDCOUNT] = {0};  // Cycles for blink timing
volatile speed LedBlinkSpeed[LEDCOUNT] = {slow};
uint32_t lastBlinkTime = 0;

typedef struct {
    gpio_num_t gpio;
    adc_oneshot_unit_handle_t handle;
    adc_channel_t channel;
} adc_config_t;

// ADC config for ESP32-WROOM, matching GPIO to channel (ADC1/2 assigned in initKnobs)
static adc_config_t adc_configs[] = {
    {GPIO_NUM_36, NULL, ADC_CHANNEL_0},  // KNOB1: GPIO36 = ADC1_CH0
    {GPIO_NUM_35, NULL, ADC_CHANNEL_7},  // KNOB2: GPIO35 = ADC1_CH7
    {GPIO_NUM_2,  NULL, ADC_CHANNEL_2},  // KNOB3: GPIO2 = ADC2_CH2
    {GPIO_NUM_0,  NULL, ADC_CHANNEL_1},  // KNOB4: GPIO0 = ADC2_CH1 (jumpered; floating if not)
    {GPIO_NUM_15, NULL, ADC_CHANNEL_3},  // KNOB5: GPIO15 = ADC2_CH3
    {GPIO_NUM_14, NULL, ADC_CHANNEL_6},  // KNOB6: GPIO14 = ADC2_CH6
    {GPIO_NUM_13, NULL, ADC_CHANNEL_4},  // KNOB7: GPIO13 = ADC2_CH4
    {GPIO_NUM_4,  NULL, ADC_CHANNEL_0}   // KNOB8: GPIO4 = ADC2_CH0
};

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

/**
 * @brief Chetu-style GetButtonsStatus: Read reg, detect edges, fire cb (no double-click).
 */
static void pollButtons(void) {
    if (!g_button_cb) {
        ESP_LOGW(TAG, "No button cb set");  // One-time
        return;
    }

    static bool first_poll = true;
    uint16_t registerValue = readButtonRegister();
    if (first_poll || registerValue != prev_button_state) {
        ESP_LOGI(TAG, "Button reg: 0x%04x", registerValue);
        first_poll = false;
    }

    for (int i = 0; i < BUTTONSCOUNT; i++) {
        buttonCurrentStatus[i] = (registerValue >> i) & 0x01;

        if (buttonCurrentStatus[i] && !buttonLastStatus[i]) {  // Press edge
            timerStart[i] = esp_timer_get_time();
        }

        if (!buttonCurrentStatus[i] && buttonLastStatus[i]) { // Release edge
            pressDuration[i] = esp_timer_get_time() - timerStart[i];
            if (pressDuration[i] > LONG_PRESS_THRESHOLD_US) {
                longPressDetected[i] = true;
                g_button_cb((uint8_t)(i + 1), LONG_PRESS);
            } else {
                g_button_cb((uint8_t)(i + 1), SHORT_PRESS);
            }
            longPressDetected[i] = false;  // Reset
        }

        buttonLastStatus[i] = buttonCurrentStatus[i];
    }
    prev_button_state = registerValue;
}

void setUILogLevel(esp_log_level_t level) {
    esp_log_level_set(TAG, level);
}

static void initButtons(void) {
    gpio_config_t shld_conf = {
        .intr_type = GPIO_INTR_DISABLE,
        .mode = GPIO_MODE_OUTPUT,
        .pin_bit_mask = (1ULL << PIN_SHLD),
    };
    ESP_ERROR_CHECK(gpio_config(&shld_conf));

    gpio_config_t qh_conf = {
        .intr_type = GPIO_INTR_DISABLE,
        .mode = GPIO_MODE_INPUT,
        .pin_bit_mask = (1ULL << PIN_QH),
        .pull_up_en = GPIO_PULLUP_ENABLE,  // Assuming internal pull-up if no external
    };
    ESP_ERROR_CHECK(gpio_config(&qh_conf));

    gpio_config_t clk_conf = {
        .intr_type = GPIO_INTR_DISABLE,
        .mode = GPIO_MODE_OUTPUT,
        .pin_bit_mask = (1ULL << PIN_CLK),
    };
    ESP_ERROR_CHECK(gpio_config(&clk_conf));
}

static void initLEDs(void) {
    gpio_config_t io_conf = {
        .intr_type = GPIO_INTR_DISABLE,
        .mode = GPIO_MODE_OUTPUT,
        .pin_bit_mask = (1ULL << PIN_MOSI) | (1ULL << PIN_SET_D),
    };
    ESP_ERROR_CHECK(gpio_config(&io_conf));

    gpio_set_level(PIN_SET_D, 0);
}

static void initKnobs(void) {
    adc_oneshot_unit_init_cfg_t adc1_cfg = {
        .unit_id = ADC_UNIT_1,
        .ulp_mode = ADC_ULP_MODE_DISABLE,
    };
    ESP_ERROR_CHECK(adc_oneshot_new_unit(&adc1_cfg, &adc1_handle));

    adc_oneshot_unit_init_cfg_t adc2_cfg = {
        .unit_id = ADC_UNIT_2,
        .ulp_mode = ADC_ULP_MODE_DISABLE,
    };
    ESP_ERROR_CHECK(adc_oneshot_new_unit(&adc2_cfg, &adc2_handle));

	for (int i = 0; i < NUM_KNOBS; i++) {
	    ESP_LOGI(TAG, "ADC%d init for GPIO%d", i+1, adc_configs[i].gpio);  // Debug init
	    adc_oneshot_chan_cfg_t chan_cfg = {
	        .atten = ADC_ATTEN_DB_12,  // Updated from deprecated DB_11
	        .bitwidth = ADC_BITWIDTH_12,
	    };
	    if (adc_configs[i].gpio >= 32 && adc_configs[i].gpio <= 39) {  // ADC1 pins
	        adc_configs[i].handle = adc1_handle;
	        ESP_ERROR_CHECK(adc_oneshot_config_channel(adc1_handle, adc_configs[i].channel, &chan_cfg));
	    } else {  // ADC2 pins
	        adc_configs[i].handle = adc2_handle;
	        ESP_ERROR_CHECK(adc_oneshot_config_channel(adc2_handle, adc_configs[i].channel, &chan_cfg));
	    }
	}
}

void initUI(void) {
    initButtons();
    initLEDs();
    initKnobs();
    memset((void*)LedState, RESET, sizeof(LedState));  // Cast to avoid volatile warning
    memset((void*)LedBlinkState, false, sizeof(LedBlinkState));  // Cast to avoid volatile warning
    memset((void*)LedBlinkCount, 0, sizeof(LedBlinkCount));  // Cast to avoid volatile warning
    memset((void*)LedBlinkSpeed, slow, sizeof(LedBlinkSpeed));  // Cast to avoid volatile warning
    lastBlinkTime = 0;
}

/**
 * @brief Set saved value for knob chasing (for patch recall).
 * @param knobNum Knob index.
 * @param value Normalized saved value (0.0-1.0).
 * @param enable_chase True to enable chasing mode.
 */
void setKnobSavedValue(knob_index_t knobNum, float value, bool enable_chase) {
    if (knobNum >= NUM_KNOBS) {
        ESP_LOGE(TAG, "Invalid knob %d", knobNum);
        return;
    }
    if (value < 0.0f || value > 1.0f) {
        ESP_LOGE(TAG, "Invalid value %.2f for knob %d", value, knobNum);
        return;
    }
    saved_knob_values[knobNum] = value;
    is_chasing[knobNum] = enable_chase;
    ESP_LOGI(TAG, "Knob %d saved value set to %.2f, chasing %s", knobNum, value, enable_chase ? "enabled" : "disabled");
}

float readKnob(knob_index_t knobNum) {
    if (knobNum >= NUM_KNOBS) {
        ESP_LOGE(TAG, "Invalid knob %d", knobNum);
        return -1.0f;
    }

	int raw = 0;
	adc_oneshot_unit_handle_t handle = adc_configs[knobNum].handle;
	adc_channel_t channel = adc_configs[knobNum].channel;
	esp_err_t err = adc_oneshot_read(handle, channel, &raw);
	if (err != ESP_OK) {
	    ESP_LOGE(TAG, "ADC read failed for knob %d: %s", knobNum, esp_err_to_name(err));
	    return -1.0f;
	}

	#define KNOB_LOG_THRESHOLD 100  // Threshold for logging significant changes
	static int last_logged_raw[NUM_KNOBS] = {-1};  // Track last logged raw value

	// Inverted per schematic with limited logging
	static int last_raw[NUM_KNOBS] = {-1};
	int inverted = 4095 - raw;
	if (abs(inverted - last_raw[knobNum]) < HYSTERESIS_THRESHOLD && last_raw[knobNum] != -1) {
	    inverted = last_raw[knobNum];
	}
	last_raw[knobNum] = inverted;

	float physical_norm = (float)inverted / 4095.0f;

	// Chasing mode
	if (is_chasing[knobNum]) {
	    if (fabs(physical_norm - saved_knob_values[knobNum]) < KNOB_CHASE_THRESHOLD) {
	        is_chasing[knobNum] = false;
	        if (abs(raw - last_logged_raw[knobNum]) > KNOB_LOG_THRESHOLD || last_logged_raw[knobNum] == -1) {
	            ESP_LOGI(TAG, "Knob %d caught up—switching to physical tracking, handle=%p, channel=%d, raw=%d",
	                     knobNum, (void*)handle, channel, raw);
	            last_logged_raw[knobNum] = raw;
	        }
	    }
	    if (abs(raw - last_logged_raw[knobNum]) > KNOB_LOG_THRESHOLD || last_logged_raw[knobNum] == -1) {
	        ESP_LOGI(TAG, "Knob %d handle=%p, channel=%d, raw=%d", knobNum, (void*)handle, channel, raw);  // Log all on change
	        last_logged_raw[knobNum] = raw;
	    }
	} else if (abs(raw - last_logged_raw[knobNum]) > KNOB_LOG_THRESHOLD || last_logged_raw[knobNum] == -1) {
	    ESP_LOGI(TAG, "Knob %d handle=%p, channel=%d, raw=%d", knobNum, (void*)handle, channel, raw);  // Log all on change
	    last_logged_raw[knobNum] = raw;
	}

	return physical_norm;
}

void shiftOutRegister(uint32_t bits_value) {
    gpio_set_level(PIN_SET_D, 0);
    for (uint8_t i = 0; i < LEDCOUNT; i++) {
        bool bitValue = (bits_value >> i) & 0x01;
        gpio_set_level(PIN_MOSI, !bitValue);  // Inverted for common anode
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
    LedBlinkCount[bitNum] = 0;  // Stop blink if active
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

void setLedState(uint8_t ledNum, led_state_t state) {
    if (ledNum >= (DUAL_LED_COUNT + SINGLE_LED_COUNT)) {
        ESP_LOGE(TAG, "Invalid LED %d", ledNum);
        return;
    }
    switch (state) {
        case LED_OFF:
            if (ledNum < DUAL_LED_COUNT) {
                uint8_t red_bit = ledNum;
                uint8_t green_bit = ledNum + DUAL_LED_COUNT;
                setLedBitState(red_bit, RESET);
                setLedBitState(green_bit, RESET);
            } else {
                setLedBitState(ledNum + 8, RESET);
            }
            break;
        case LED_ON:
            if (ledNum < DUAL_LED_COUNT) {
                blinkLED(ledNum, slow, yellow);  // Default to yellow for dual
            } else {
                setLedBitState(ledNum + 8, SET);
            }
            break;
        case LED_BLINK_FAST:
            if (ledNum < DUAL_LED_COUNT) {
                blinkLED(ledNum, fast, redGreenYellow);  // Default pattern for dual
            } else {
                blinkLedBit(ledNum + 8, fast);
            }
            break;
        case LED_BLINK_SLOW:
            if (ledNum < DUAL_LED_COUNT) {
                blinkLED(ledNum, slow, redGreenYellow);
            } else {
                blinkLedBit(ledNum + 8, slow);
            }
            break;
        default:
            ESP_LOGE(TAG, "Invalid state %d", state);
            break;
    }
}

#ifdef ADVANCED_UI
void setLedAdvanced(uint8_t ledNum, led_state_t baseState, float duty, const char* pattern) {
    // Implement advanced features here, e.g., custom duty cycle blinks or morse
    // For duty: Adjust LedBlinkCount based on duty * interval
    // For pattern: Parse morse string to sequence blinks
    ESP_LOGW(TAG, "Advanced LED not implemented yet");
}
#endif

void setButtonCallback(button_callback_t cb) {
    g_button_cb = cb;
    ESP_LOGI(TAG, "Button cb set");
}

void testUI(void) {
    // Demo simple API
    for (uint8_t i = 0; i < DUAL_LED_COUNT-5; i++) {
        setLedState(i, LED_BLINK_SLOW);  // Dual slow blink (yellow/redGreenYellow)
    }
    for (uint8_t i = DUAL_LED_COUNT; i < (DUAL_LED_COUNT + SINGLE_LED_COUNT-10); i++) {
        setLedState(i, LED_BLINK_FAST);
    }
    ESP_LOGI(TAG, "LED test activated with simple API");
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

bool isButtonPressed(uint8_t btnNum) {
    if (btnNum < 1 || btnNum > BUTTONSCOUNT) {
        ESP_LOGE(TAG, "Invalid button %d", btnNum);
        return false;
    }
    return buttonCurrentStatus[btnNum - 1];
}