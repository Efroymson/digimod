#pragma once
#ifdef __cplusplus
extern "C" {
#endif

#define S1      1
#define S2      2
#define S3      3
#define S4      4
#define S5      5
#define S6      6
#define S7      7
#define S8      8
#define S9      9
#define S10     10
#define S11     11
#define S12     12
#define S13     13
#define S14     14
#define S15     15
#define S16     16

#define LED1    1
#define LED2    2
#define LED3    3
#define LED4    4
#define LED5    5
#define LED6    6
#define LED7    7  
#define LED8    8
#define LED9    9
#define LED10   10
#define LED11   11
#define LED12   12
#define LED13   13
#define LED14   14
#define LED15   15
#define LED16   16

#define MAX_ADC 8

#define GPIO_OUTPUT_IO_3 ((gpio_num_t) 3) // SH/LD PIN
#define GPIO_OUTPUT_IO_16 ((gpio_num_t) 16) // CLK PIN
#define GPIO_INPUT_IO_5  ((gpio_num_t) 5) // QH PIN aka MISO
#define GPIO_OUTPUT_IO_32 ((gpio_num_t) 32) // MOSI
#define GPIO_OUTPUT_IO_33 ((gpio_num_t) 33) // SET_D

#define PIN_SHLD GPIO_OUTPUT_IO_3
#define PIN_CLK GPIO_OUTPUT_IO_16
#define PIN_QH GPIO_INPUT_IO_5
#define PIN_MOSI GPIO_OUTPUT_IO_32
#define PIN_SET_D GPIO_OUTPUT_IO_33
#define GPIO_OUTPUT_PIN_SEL ((1ULL<<PIN_SHLD) | (1ULL<<PIN_CLK) | (1ULL<<PIN_MOSI) | (1ULL<<PIN_SET_D))
#define GPIO_INPUT_PIN_SEL  ((1<<PIN_QH))
#define DOUBLE_CLICK_THRESHOLD 500000 // 500000 microseconds = 0.5 seconds

static uint32_t LedRegValue = 0 ;

typedef enum{
    ADC1,
    ADC2,
    ADC3,
    ADC4,
    ADC5,
    ADC6,
    ADC7,
    ADC8
} ADC_CHENAL; 

// Enum for press types
typedef enum {
    SHORT_PRESS,
    LONG_PRESS,
    DOUBLE_CLICK
} PressType;

typedef enum {
    SET,
    RESET,
    BLINK,
    FAST_BLINK,
    SLOW_BLINK
} StateType;

bool buttonPressed(int switchNumber, PressType pressType);
void initButtonPotLED(uint8_t ButtonCount, uint8_t PotCount, uint8_t LEDCount, void (*f1)(uint8_t, PressType));
void shiftOutRegister(uint32_t bits_value);
uint16_t readShiftRegister(void);
void SetLedState(uint8_t LedNumber, StateType state);
static void UpdateButtonPotLED(void * ptr);
static void UpdateLED(void);
static uint8_t GetButtonsStatus(void);
void UpdatePOT(void);

#ifdef __cplusplus
}
#endif
