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

#define NUM_KNOBS 16  // Expanded to 16 (8 physical + 8 virtual)
#define KNOB_MODES 2  // Default, btn-held
// Knob chasing globals per mode
static float saved_knob_values[NUM_KNOBS][KNOB_MODES] = {{0.5f, 0.5f}};  // Default mid
static bool isChasing[NUM_KNOBS][KNOB_MODES] = {{false, false}};        // Explicit chasing control

static struct {
    knob_index_t phys_knob;
    knob_index_t virt_knob;
    uint8_t btn;
} multi_knob_map[NUM_KNOBS] = {{0}};  // Mapping: phys->virt, button

#define PIN_MOSI GPIO_NUM_32
#define PIN_CLK  GPIO_NUM_16
#define PIN_SET_D GPIO_NUM_33
#define PIN_SHLD GPIO_NUM_3  // Schematic: Output for 74HC165 PL (latch/load)
#define PIN_QH   GPIO_NUM_5  // Schematic: Input for Q7 (serial out, high=pressed)

static const char *TAG = "UI";

volatile uint8_t knobsUpdated = 0;  // Global flag for knob changes

// Button globals (simplified, no double-click)
static bool buttonCurrentStatus[BUTTONSCOUNT] = {false};
static bool buttonLastStatus[BUTTONSCOUNT] = {false};
static uint64_t timerStart[BUTTONSCOUNT] = {0};  // Used for long press start time
static uint64_t pressDuration[BUTTONSCOUNT] = {0};  // Used for long press duration
static bool longPressDetected[BUTTONSCOUNT] = {false};  // Flag for long press
static uint16_t prev_button_state = 0;  // For reg change log
static button_callback_t g_button_cb = NULL;  // Global cb

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
    {GPIO_NUM_36, NULL, ADC_CHANNEL_0},  // KNOB1: ADC1_CH0
    {GPIO_NUM_35, NULL, ADC_CHANNEL_7},  // KNOB2: ADC1_CH7
    {GPIO_NUM_2,  NULL, ADC_CHANNEL_2},  // KNOB3: ADC2_CH2
    {GPIO_NUM_0,  NULL, ADC_CHANNEL_1},  // KNOB4: ADC2_CH1 (jumpered)
    {GPIO_NUM_15, NULL, ADC_CHANNEL_3},  // KNOB5: ADC2_CH3
    {GPIO_NUM_14, NULL, ADC_CHANNEL_6},  // KNOB6: ADC2_CH6
    {GPIO_NUM_13, NULL, ADC_CHANNEL_4},  // KNOB7: ADC2_CH4
    {GPIO_NUM_4,  NULL, ADC_CHANNEL_0},  // KNOB8: ADC2_CH0
    // Virtual knobs (use physical knob's config)
    {0, NULL, 0}, {0, NULL, 0}, {0, NULL, 0}, {0, NULL, 0},  // KNOB9-12
    {0, NULL, 0}, {0, NULL, 0}, {0, NULL, 0}, {0, NULL, 0}   // KNOB13-16
};

static adc_oneshot_unit_handle_t adc1_handle;
static adc_oneshot_unit_handle_t adc2_handle;

// Per-knob param pointers (NULL if not registered)
static volatile float* knob_params[NUM_KNOBS] = {NULL};

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

    adc_oneshot_chan_cfg_t chan_cfg = {
        .atten = ADC_ATTEN_DB_12,  // Updated for deprecation
        .bitwidth = ADC_BITWIDTH_12,
    };
    for (int i = 0; i < NUM_KNOBS; i++) {
        if (adc_configs[i].gpio == 0) continue;  // Skip virtual if not configured
        adc_configs[i].handle = (i < 2) ? adc1_handle : adc2_handle;  // Fixed: KNOB1/2 on ADC1, KNOB3+ on ADC2
        ESP_ERROR_CHECK(adc_oneshot_config_channel(adc_configs[i].handle, adc_configs[i].channel, &chan_cfg));
    }
    // Initial read to set baseline for registered knobs
    for (int i = 0; i < NUM_KNOBS; i++) {
        if (knob_params[i]) {
            float init_val = readKnob(i);
            ESP_LOGI(TAG, "Initial read for knob %d: %.2f", i, init_val);
        }
    }
}

void initUI(void) {
    initButtons();
    initLEDs();
    initKnobs();
}

float readKnob(knob_index_t knobNum) {
    if (knobNum >= NUM_KNOBS) {
        ESP_LOGE(TAG, "Invalid knob %d", knobNum);
        return -1.0f;
    }

    int raw;
    if (adc_oneshot_read(adc_configs[knobNum].handle, adc_configs[knobNum].channel, &raw) != ESP_OK) {
        ESP_LOGE(TAG, "ADC read failed for knob %d", knobNum);
        return -1.0f;
    }
    ESP_LOGD(TAG, "Knob %d raw ADC: %d", knobNum, raw);  // LOGD to reduce spam

    float norm = (4095.0f - (float)raw) / 4095.0f;  // Inverted, normalized 0-1
    uint8_t mode = 0;
    for (int i = 0; i < NUM_KNOBS; i++) {
        if (multi_knob_map[i].phys_knob == knobNum && multi_knob_map[i].btn != 0 && isButtonPressed(multi_knob_map[i].btn)) {
            mode = 1;
            knobNum = multi_knob_map[i].virt_knob;
            break;
        }
    }

    if (isChasing[knobNum][mode]) {
        float saved = saved_knob_values[knobNum][mode];
        float diff = fabs(norm - saved);
        if (diff > KNOB_CHASE_THRESHOLD) {
            ESP_LOGD(TAG, "Chasing knob %d (mode %d): diff %.2f > threshold, holding %.2f", knobNum, mode, diff, saved);
            return saved;
        } else {
            float averaged = (saved + norm) / 2.0f;
            saved_knob_values[knobNum][mode] = averaged;
            isChasing[knobNum][mode] = false;
            ESP_LOGI(TAG, "Chasing knob %d (mode %d): picked up, averaged to %.2f", knobNum, mode, averaged);
            return averaged;
        }
    }
    return norm;
}

void setKnobSavedValue(knob_index_t knobNum, float value, uint8_t mode, bool enable_chase) {
    if (knobNum >= NUM_KNOBS || mode >= KNOB_MODES) {
        ESP_LOGE(TAG, "Invalid knob %d or mode %d", knobNum, mode);
        return;
    }
    saved_knob_values[knobNum][mode] = (value >= 0.0f && value <= 1.0f) ? value : 0.5f;
    isChasing[knobNum][mode] = enable_chase;
    // Sync chasing for virtual pairs (phys/virt)
    for (int i = 0; i < NUM_KNOBS; i++) {
        if (multi_knob_map[i].phys_knob == knobNum) {
            isChasing[multi_knob_map[i].virt_knob][mode] = enable_chase;
        } else if (multi_knob_map[i].virt_knob == knobNum) {
            isChasing[multi_knob_map[i].phys_knob][mode] = enable_chase;
        }
    }
    ESP_LOGI(TAG, "Knob %d (mode %d) saved: %.2f, chasing %s", knobNum, mode, saved_knob_values[knobNum][mode], enable_chase ? "enabled" : "disabled");
}

void setKnobParam(knob_index_t knobNum, volatile float* paramPtr) {
    if (knobNum >= NUM_KNOBS) {
        ESP_LOGE(TAG, "Invalid knob %d", knobNum);
        return;
    }
    knob_params[knobNum] = paramPtr;
    ESP_LOGI(TAG, "Param pointer registered for knob %d at %p", knobNum, (void*)paramPtr);
}

void initMultiKnob(knob_index_t phys_knob, knob_index_t virt_knob, uint8_t btn) {
    if (phys_knob >= NUM_KNOBS || virt_knob >= NUM_KNOBS) {
        ESP_LOGE(TAG, "Invalid knob %d or %d", phys_knob + 1, virt_knob + 1);
        return;
    }
    if (btn != 0 && (btn < 1 || btn > BUTTONSCOUNT)) {
        ESP_LOGE(TAG, "Invalid button %d", btn);
        return;
    }
    for (int i = 0; i < NUM_KNOBS; i++) {
        if (multi_knob_map[i].phys_knob == 0) {
            multi_knob_map[i].phys_knob = phys_knob;
            multi_knob_map[i].virt_knob = virt_knob;
            multi_knob_map[i].btn = btn;
            ESP_LOGI(TAG, "Multi-knob %d mapped to virtual %d with btn=%d", phys_knob + 1, virt_knob + 1, btn);
            break;
        }
    }
}

void shiftOutRegister(uint32_t bits_value) {
    gpio_set_level(PIN_SET_D, 0);  // Clear shift register
    for (int i = 31; i >= 0; i--) {
        gpio_set_level(PIN_CLK, 0);
        gpio_set_level(PIN_MOSI, (bits_value & (1U << i)) ? 1 : 0);  // Inverted for common anode
        gpio_set_level(PIN_CLK, 1);
    }
    gpio_set_level(PIN_SET_D, 1);  // Latch
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
    ESP_LOGW(TAG, "Advanced LED not implemented yet");
}
#endif

void setLedBitState(uint8_t bitNum, StateType state) {
    if (bitNum >= LEDCOUNT) {
        ESP_LOGE(TAG, "Bit %d out of range", bitNum);
        return;
    }
    LedState[bitNum] = state;
}

void blinkLedBit(uint8_t bitNum, speed blinkSpeed) {
    if (bitNum >= LEDCOUNT) {
        ESP_LOGE(TAG, "Bit %d out of range", bitNum);
        return;
    }
    LedBlinkSpeed[bitNum] = blinkSpeed;
    LedBlinkCount[bitNum] = (blinkSpeed == fast) ? FAST_BLINK_INTERVAL_MS : SLOW_BLINK_INTERVAL_MS;
    LedBlinkState[bitNum] = true;
}

void blinkLED(uint8_t ledNum, speed blinkSpeed, colorPattern pattern) {
    if (ledNum >= DUAL_LED_COUNT) {
        blinkLedBit(ledNum + 8, blinkSpeed);
        return;
    }
    uint8_t red_bit = ledNum;
    uint8_t green_bit = ledNum + DUAL_LED_COUNT;
    switch (pattern) {
        case redGreenYellow:
            setLedBitState(green_bit, SET);
            blinkLedBit(red_bit, blinkSpeed);
            return;
        case redGreen:
            setLedBitState(green_bit, SET);
            blinkLedBit(red_bit, blinkSpeed);
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
}

void setButtonCallback(button_callback_t cb) {
    g_button_cb = cb;
    ESP_LOGI(TAG, "Button cb set");
}

void testUI(void *) {
    while (1) {
        for (uint8_t i = 0; i < DUAL_LED_COUNT - 5; i++) {
            setLedState(i, LED_BLINK_SLOW);  // Dual slow blink
        }
        for (uint8_t i = DUAL_LED_COUNT; i < (DUAL_LED_COUNT + SINGLE_LED_COUNT - 10); i++) {
            setLedState(i, LED_BLINK_FAST);
        }
        ESP_LOGI(TAG, "LED test activated with simple API");
        vTaskDelay(pdMS_TO_TICKS(1000));  // Change every second

        for (uint8_t i = 5; i < DUAL_LED_COUNT; i++) {
            blinkLED(i, fast, greenYellow);  // Dual fast blink
        }
        for (uint8_t i = DUAL_LED_COUNT + 10; i < (DUAL_LED_COUNT + SINGLE_LED_COUNT); i++) {
            setLedState(i, LED_BLINK_SLOW);
        }
        ESP_LOGI(TAG, "LED test activated with different simple API");
        vTaskDelay(pdMS_TO_TICKS(1000));  // Change every second
    }
}

void updateUITask(void *pvParameters) {
    ESP_LOGI(TAG, "UI task started on core %d", xPortGetCoreID());
    TickType_t last_wake = xTaskGetTickCount();
    uint32_t last_led_bits = 0;

    static float last_knob_values[NUM_KNOBS] = { -1.0f };  // For change detection

    while (1) {
        // Poll buttons first
        pollButtons();

        // Poll only registered knobs
        for (knob_index_t i = 0; i < NUM_KNOBS; i++) {
            if (knob_params[i] == NULL) continue;  // Skip unregistered

            float val = readKnob(i);
            if (val < 0.0f) {
                ESP_LOGE(TAG, "Error reading knob %d", i);
                continue;
            }

            ESP_LOGD(TAG, "Knob %d checked: val %.2f, last %.2f", i, val, last_knob_values[i]);  // LOGD to reduce spam
            if (fabs(val - last_knob_values[i]) > (HYSTERESIS_THRESHOLD / 4095.0f)) {  // Increased to 30 for noise filter
                ESP_LOGI(TAG, "Knob %d raw value changed to %.2f", i, val);  // LOGI for changes
                last_knob_values[i] = val;
                *knob_params[i] = val;  // Direct update
                knobsUpdated = 1;
                ESP_LOGI(TAG, "Knob %d param updated to %.2f", i, val);  // LOGI for updates
            }
        }


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