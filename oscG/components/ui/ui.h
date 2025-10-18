#ifndef UI_H
#define UI_H

#include <stdint.h>
#include "driver/gpio.h"
#include "esp_adc/adc_oneshot.h"
#include "freertos/FreeRTOS.h"
#include "esp_log.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum { SET, RESET } StateType;  // Retained for internal use
typedef enum { fast, slow } speed;  // Retained for internal use
typedef enum { redGreenYellow, redGreen, redYellow, greenYellow, red, green, yellow } colorPattern;  // Retained for advanced

#define LEDCOUNT 32
#define DUAL_LED_COUNT 8
#define SINGLE_LED_COUNT 16  // Bits 16-31 (total 32)
#define BUTTONSCOUNT 16
#define HYSTERESIS_THRESHOLD 90.0f  // User-tuned for smoothness
#define FAST_BLINK_INTERVAL_MS 100
#define SLOW_BLINK_INTERVAL_MS 500
#define UI_UPDATE_INTERVAL_MS 50  // was 10, too fast??
#define LONG_PRESS_THRESHOLD_US 1000000   // 1s
#define KNOB_CHASE_THRESHOLD 0.05f  // 5% closeness

typedef enum { 
    KNOB1 = 0, KNOB2, KNOB3, KNOB4, KNOB5, KNOB6, KNOB7, KNOB8, 
    KNOB9 = 8, KNOB10, KNOB11, KNOB12, KNOB13, KNOB14, KNOB15, KNOB16 
} knob_index_t;  // Expanded to 16 (8 physical + 8 virtual)
#define NUM_KNOBS 16  // Total knob slots (physical + virtual)

typedef enum {
    SHORT_PRESS,
    LONG_PRESS
} PressType;  // Removed DOUBLE_CLICK

/**
 * @brief Simple LED state enum for basic control.
 */
typedef enum { LED_OFF, LED_ON, LED_BLINK_FAST, LED_BLINK_SLOW } led_state_t;

/**
 * @brief Per-button state for edge detection and timing (legacy from chetu).
 */
typedef struct {
    bool is_pressed;          // Current press status
    uint64_t press_start_us;  // Timestamp on rising edge
    uint64_t last_release_us; // Timestamp on last falling edge (unused now)
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
 * @param pressType Detected type (SHORT_PRESS or LONG_PRESS).
 */
typedef void (*button_callback_t)(uint8_t buttonNum, PressType pressType);

void testUI(void *);

void initUI(void);
/**
 * @brief Read knob value as normalized float (0.0-1.0), with chasing mode.
 * @param knobNum Knob index.
 * @return Normalized value, or -1.0 on error.
 */
float readKnob(knob_index_t knobNum);

/**
 * @brief Set saved value for knob chasing (for patch recall).
 * @param knobNum Knob index (physical or virtual).
 * @param value Normalized saved value (0.0-1.0).
 * @param mode Mode (0: default, 1: btn-held).
 * @param enable_chase True to enable chasing.
 */
void setKnobSavedValue(knob_index_t knobNum, float value, uint8_t mode, bool enable_chase);

/**
 * @brief Register a parameter pointer for a knob (updated on change).
 * @param knobNum Knob index.
 * @param paramPtr Pointer to float param to update.
 */
void setKnobParam(knob_index_t knobNum, volatile float* paramPtr);

/**
 * @brief Initialize multi-mode knob with a button for a virtual knob.
 * @param phys_knob Physical knob index.
 * @param virt_knob Virtual knob index for button-held mode.
 * @param btn Button to toggle virtual mode (0 if unused).
 */
void initMultiKnob(knob_index_t phys_knob, knob_index_t virt_knob, uint8_t btn);

void shiftOutRegister(uint32_t bits_value);
/**
 * @brief Simple LED state setter.
 * @param ledNum LED index (0-23: 0-7 dual, 8-23 single).
 * @param state Basic state enum.
 */
void setLedState(uint8_t ledNum, led_state_t state);

#ifdef ADVANCED_UI
/**
 * @brief Advanced LED control for patterns, duty, etc.
 * @param ledNum LED index.
 * @param baseState Base state.
 * @param duty Duty cycle (0.0-1.0, default 0.5).
 * @param pattern Morse code string or NULL.
 */
void setLedAdvanced(uint8_t ledNum, led_state_t baseState, float duty, const char* pattern);
#endif

// Retained for compatibility/advanced use
void setLedBitState(uint8_t bitNum, StateType state);
void blinkLedBit(uint8_t bitNum, speed blinkSpeed);
void blinkLED(uint8_t ledNum, speed blinkSpeed, colorPattern pattern);

void updateUITask(void *pvParameters);
void setUILogLevel(esp_log_level_t level);
void testUI(void *);  // LED test only (buttons always polled post-init)

/**
 * @brief Set global button callback (call in app_main before UI task).
 * @param cb Callback function.
 */
void setButtonCallback(button_callback_t cb);
/**
 * @brief Check if button is currently pressed.
 * @param btnNum 1-based button index (1-16).
 * @return True if pressed, false otherwise.
 */
bool isButtonPressed(uint8_t btnNum);

extern volatile uint8_t knobsUpdated;  // Flag set when any knob changes

#ifdef __cplusplus
}
#endif

#endif // UI_H