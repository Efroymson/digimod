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
#define SINGLE_LED_COUNT 24  // Adjusted for full 32 LEDs
#define HYSTERESIS_THRESHOLD 50
#define FAST_BLINK_INTERVAL_MS 100
#define SLOW_BLINK_INTERVAL_MS 500
#define UI_UPDATE_INTERVAL_MS 10  // 10ms update rate

typedef enum { ADC1 = 0, ADC3, ADC5, ADC6, ADC7, ADC8 } adc_index_t;

extern volatile StateType LedState[LEDCOUNT];
extern volatile bool LedBlinkState[LEDCOUNT];
extern volatile uint32_t LedBlinkCount[LEDCOUNT];  // Count for blink timing
extern uint32_t lastBlinkTime;

void initUI(void);
int readADC(adc_index_t adcNum);
void shiftOutRegister(uint32_t bits_value);
void setLedBitState(uint8_t bitNum, StateType state);
void blinkLedBit(uint8_t bitNum, speed blinkSpeed);
void blinkLED(uint8_t ledNum, speed blinkSpeed, colorPattern pattern);
void updateUITask(void *pvParameters);

#ifdef __cplusplus
}
#endif

#endif // UI_H