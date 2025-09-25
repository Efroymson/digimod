#ifndef UI_H
#define UI_H

#include <stdint.h>
#include "driver/gpio.h"
#include "esp_adc/adc_oneshot.h"
#include "freertos/FreeRTOS.h"  // For TickType_t and xTaskGetTickCount

#ifdef __cplusplus
extern "C" {
#endif

// Enums for LED states and patterns
typedef enum {
    SET,
    RESET,
    BLINK,
    SLOW_BLINK,
    FAST_BLINK
} StateType;

typedef enum {
    fast,
    slow
} speed;

typedef enum {
    redGreenYellow,
    redGreen,
    redYellow,
    greenYellow,
    red,
    green,
    yellow
} colorPattern;

// Constants
#define LEDCOUNT 32
#define DUAL_LED_COUNT 8  // First 8 dual-color LEDs
#define BUTTONSCOUNT 16
#define DOUBLE_CLICK_THRESHOLD 300000  // 300ms in microseconds (for future button use)
#define HYSTERESIS_THRESHOLD 50  // Threshold for stable pot readings
#define FAST_BLINK_INTERVAL 100  // 100ms for fast blink
#define SLOW_BLINK_INTERVAL 500  // 500ms for slow blink

// ADC indices
typedef enum {
    ADC1 = 0,  // GPIO36
    ADC3 = 1,  // GPIO2
    ADC5 = 2,  // GPIO13
    ADC6 = 3,  // GPIO14
    ADC7 = 4,  // GPIO4
    ADC8 = 5   // GPIO15
} adc_index_t;

// External globals for LED state (shared with updateUITask)
extern volatile StateType LedState[LEDCOUNT];
extern volatile bool LedBlinkState[LEDCOUNT];
extern uint32_t lastBlinkTime;

// Function prototypes
void initUI(void);  // Unified initialization for ADC, GPIO, and 74HC595
int readADC(adc_index_t adcNum);  // Read ADC value for given index
void shiftOutRegister(uint32_t bits_value);  // LED update function
void blinkLED(uint8_t ledNum, speed blinkSpeed, colorPattern pattern);  // LED control API

#ifdef __cplusplus
}
#endif

#endif // UI_H