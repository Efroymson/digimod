#ifndef UI_H
#define UI_H

#include <stdint.h>
#include "driver/gpio.h"
#include "esp_adc/adc_oneshot.h"

#ifdef __cplusplus
extern "C" {
#endif

// Function prototypes
void initMinimalADC(void);
int readADC1(void);  // Pot 1 (GPIO36)
int readADC3(void);  // Pot 3 (GPIO2)
int readADC5(void);  // Pot 5 (GPIO13)
int readADC6(void);  // Pot 6 (GPIO14)
int readADC7(void);  // Pot 7 (GPIO4)
int readADC8(void);  // Pot 8 (GPIO15)

#ifdef __cplusplus
}
#endif

#endif // UI_H