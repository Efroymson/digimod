#ifndef UI_H
#define UI_H

#include <stdint.h>
#include "driver/gpio.h"
#include "esp_adc/adc_oneshot.h"
#include "freertos/FreeRTOS.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum { SET, RESET } StateType;
typedef enum { fast, slow } speed;
typedef enum { redGreenYellow, redGreen, redYellow, greenYellow, red, green, yellow } colorPattern;

#define LEDCOUNT 32
#define DUAL_LED_COUNT 8
#define SINGLE_LED_COUNT 16  // Bits 16-31 (total 32)
#define BUTTONSCOUNT 16
#define HYSTERESIS_THRESHOLD 50
#define FAST_BLINK_INTERVAL_MS 100
#define SLOW_BLINK_INTERVAL_MS 500
#define UI_UPDATE_INTERVAL_MS 10
#define DOUBLE_CLICK_THRESHOLD_US 500000  // 500ms
#define LONG_PRESS_THRESHOLD_US 1000000   // 1s

typedef enum { ADC1 = 0, ADC3, ADC5, ADC6, ADC7, ADC8 } adc_index_t;

typedef enum {
    SHORT_PRESS,
    LONG_PRESS,
    DOUBLE_CLICK
} PressType;

/**
 * @brief Per-button state for edge detection and timing (legacy from chetu).
 */
typedef struct {
    bool is_pressed;          // Current press status
    uint64_t press_start_us;  // Timestamp on rising edge
    uint64_t last_release_us; // Timestamp on last falling edge (for double-click)
} button_state_t;

extern volatile StateType LedState[LEDCOUNT];
extern volatile bool LedBlinkState[LEDCOUNT];
extern volatile uint32_t LedBlinkCount[LEDCOUNT];
extern volatile speed LedBlinkSpeed[LEDCOUNT];
extern uint32_t lastBlinkTime;
extern button_state_t buttons[BUTTONSCOUNT];  // Button states (unused in chetu-style poll)

/**
 * @brief Button event callback.
 * @param buttonNum 1-based index (1-16).
 * @param pressType Detected type.
 */
typedef void (*button_callback_t)(uint8_t buttonNum, PressType pressType);

void initUI(void);
int readADC(adc_index_t adcNum);
void shiftOutRegister(uint32_t bits_value);
void setLedBitState(uint8_t bitNum, StateType state);
void blinkLedBit(uint8_t bitNum, speed blinkSpeed);
void blinkLED(uint8_t ledNum, speed blinkSpeed, colorPattern pattern);
void updateUITask(void *pvParameters);
void setUILogLevel(esp_log_level_t level);
void testUI(void);  // LED test only (buttons always polled post-init)

/**
 * @brief Set global button callback (call in app_main before UI task).
 * @param cb Callback function.
 */
void setButtonCallback(button_callback_t cb);

#ifdef __cplusplus
}
#endif

#endif // UI_H