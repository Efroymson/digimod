#include <stdio.h>
#include <stdInt.h>
#include <esp_timer.h>
#include "esp_log.h"
#include "driver/gpio.h"
#include "driver/adc.h"
#include "ui.h"
#include "driver/timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define LEDCOUNT 		32 
#define BUTTONSCOUNT 	16

static const char *TAG = "UI";

void (*functionPtr)(uint8_t, PressType);

int rawValue;
uint16_t currentState 		= 0;
uint16_t previousState[16] 	= {0};
uint64_t timerStart[16] 	= {0};
uint64_t pressDuration[16] 	= {0};
uint64_t lastPressTime[16] 	= {0};
bool longPressDetected[16] 	= {false};
int Adc_value[MAX_ADC] 		= {0};

volatile bool LedStatus[LEDCOUNT] = {};
volatile StateType LedState[LEDCOUNT] = {};

volatile bool buttonLastStatus[BUTTONSCOUNT] = {};
volatile bool buttonCurrentStatus[BUTTONSCOUNT] = {};

uint64_t timerStart1;
gpio_config_t io_conf;

void initButtonPotLED(uint8_t ButtonCount, uint8_t PotCount, uint8_t LEDCount, void (*f1)(uint8_t, PressType))
{
	functionPtr = f1;
	io_conf.intr_type = GPIO_PIN_INTR_DISABLE;
	io_conf.mode = GPIO_MODE_OUTPUT;
	io_conf.pin_bit_mask = GPIO_OUTPUT_PIN_SEL;
	io_conf.pull_down_en = GPIO_PULLDOWN_DISABLE;
	io_conf.pull_up_en = GPIO_PULLUP_DISABLE;
	gpio_config(&io_conf);
	ESP_LOGE(TAG,"GPIO initilazation Done!...\n");
	io_conf.pin_bit_mask = GPIO_INPUT_PIN_SEL;
	io_conf.mode = GPIO_MODE_INPUT;
	io_conf.pull_down_en = GPIO_PULLDOWN_DISABLE;
	io_conf.pull_up_en = GPIO_PULLUP_DISABLE;
	gpio_config(&io_conf);
	adc1_config_width(ADC_WIDTH_BIT_12);
	adc1_config_channel_atten(ADC1_CHANNEL_0, ADC_ATTEN_DB_11);//ADC1 
	adc1_config_channel_atten(ADC1_CHANNEL_7, ADC_ATTEN_DB_11);//ADC2
	adc2_config_channel_atten(ADC2_CHANNEL_3, ADC_ATTEN_11db);//ADC3
	adc2_config_channel_atten(ADC2_CHANNEL_6, ADC_ATTEN_11db);//ADC4
	adc2_config_channel_atten(ADC2_CHANNEL_4, ADC_ATTEN_11db);//ADC5
	adc2_config_channel_atten(ADC2_CHANNEL_1, ADC_ATTEN_11db);//ADC6
	adc2_config_channel_atten(ADC2_CHANNEL_2, ADC_ATTEN_11db);//ADC7
	adc2_config_channel_atten(ADC2_CHANNEL_0, ADC_ATTEN_11db);//ADC8
	xTaskCreatePinnedToCore(UpdateButtonPotLED, "UpdateButtonPotLED", 4096, NULL, 2, NULL, 0);
}

uint16_t readShiftRegister(void)
{
    gpio_set_level(PIN_SHLD, 0);
	gpio_set_level(PIN_CLK, 0);
	gpio_set_level(PIN_CLK, 1);
	//SHLD must be high to realize the shift function
	gpio_set_level(PIN_SHLD, 1);
	int16_t Switch_value = 0;
	//Reading from QH
	for(int i=0; i<BUTTONSCOUNT; i++)
	{
		if (gpio_get_level(PIN_QH)) 
		{
			Switch_value = Switch_value | (1<<i);
		}
		//CLK INH PIN must be low. Toggle CLK to shift data into QH
		gpio_set_level(PIN_CLK, 0);
		gpio_set_level(PIN_CLK, 1);
	}
    return Switch_value;
}


// Function to detect presses and return true or false for different press types
bool buttonPressed(int switchNumber, PressType pressType) 
{
    currentState = readShiftRegister();

    if (switchNumber < 1 || switchNumber > 16) 
	{
        // Invalid switch number, return false
        return false;
    }
	bool isPressDetected = false;
    switch (pressType) 
	{
        case SHORT_PRESS:
            if (currentState & (1 << (switchNumber - 1)) && !(previousState[switchNumber - 1] & (1 << (switchNumber - 1)))) 
			{
                // Short press detected
				isPressDetected=true;
            }
            break;
        case LONG_PRESS:
			if (currentState & (1 << (switchNumber - 1)) && !(previousState[switchNumber - 1] & (1 << (switchNumber - 1)))) 
			{
				timerStart[switchNumber - 1] = esp_timer_get_time();
			}
            if (!(currentState & (1 << (switchNumber - 1))) && (previousState[switchNumber - 1] & (1 << (switchNumber - 1))) && !longPressDetected[switchNumber - 1]) 
			{
                // Long press detected
				pressDuration[switchNumber - 1] = esp_timer_get_time() - timerStart[switchNumber -1 ]; // Calculate press duration
				// Determine short or long press
				if (pressDuration[switchNumber - 1] > 1000000) 
				{ 
					longPressDetected[switchNumber - 1] = true; // Set the long press flag
					isPressDetected = true;
				}
			}	
            else if (currentState & (1 << (switchNumber - 1)) && !(previousState[switchNumber - 1] & (1 << (switchNumber - 1)))) 
			{
                // The button has been released, clear the long press flag
                longPressDetected[switchNumber - 1] = false;
            }
            break;
        case DOUBLE_CLICK:
            // Double-click detection logic
            if (currentState & (1 << (switchNumber - 1)) && !(previousState[switchNumber - 1] & (1 << (switchNumber - 1)))) 
			{
                uint64_t timeSinceLastPress = esp_timer_get_time() - lastPressTime[switchNumber - 1];
                if (timeSinceLastPress < DOUBLE_CLICK_THRESHOLD) 
				{
                    // Double click detected
					isPressDetected=true;
				}
                lastPressTime[switchNumber - 1] = esp_timer_get_time();
            }
            break;
        default:
            break;
    }

    // Store the current state as the previous state for the next iteration
    previousState[switchNumber - 1] = currentState;
    // Press type not detected, return false
    return isPressDetected;
}


void SetLedState(uint8_t LedNumber, StateType state)
{
	LedState[LedNumber-1] = state;
	switch (state)
	{
		case SET:
			{
				LedStatus[LedNumber-1] = true;
				break;
			}
		case RESET:
			{
				LedStatus[LedNumber-1] = false;
				break;
			}
		default:
			break;
	}
}

void shiftOutRegister(uint32_t bits_value)
	{
		uint8_t i;
		gpio_set_level(PIN_SET_D, 0); 
		for (i=0; i<LEDCOUNT; i++)
		{
			bool bitValue = (bits_value >> i) & 0x01;
			gpio_set_level(PIN_MOSI, !(bitValue)); // output left most bit ~ because commone anode
            gpio_set_level(PIN_CLK, 0); //tickle clock
            gpio_set_level(PIN_CLK, 1);
		}
		gpio_set_level(PIN_SET_D,1);
		gpio_set_level(PIN_SET_D,0);
	}

static void UpdateButtonPotLED(void *ptr)
{
    const TickType_t taskPeriod = 50;
    TickType_t xLastWakeTime;
	xLastWakeTime = xTaskGetTickCount();
    while (1) 
	{
		GetButtonsStatus();
		UpdateLED();
		UpdatePOT();
		vTaskDelayUntil(&xLastWakeTime, taskPeriod);
	}
}

static void UpdateLED(void)
{
	static uint8_t wait;
	for(int i=0; i<LEDCOUNT ;i++)
	{
		switch (LedState[i])
		{
		case BLINK:
			{
				if(wait == 10)
				{
					LedStatus[i] =! LedStatus[i];
				}
				break;
			}
		case SLOW_BLINK:
			{
				if(wait==5 || wait==10)
				{
					LedStatus[i] =! LedStatus[i];
				}
				break;
			}
		case FAST_BLINK:
			{
				
				LedStatus[i] =! LedStatus[i];
				break;
			}
		default:
			break;
		}

		if(LedStatus[i])
		{
			LedRegValue = LedRegValue|((1<<i));
		}
		else
		{
			LedRegValue = LedRegValue&(~(1<<i));
		}
	}
	shiftOutRegister(LedRegValue);
	wait++;
	if(wait == 11)
	{
		wait = 0;
	}
}

static uint8_t GetButtonsStatus(void)
{
	uint16_t registerValue = readShiftRegister();
	for(int i=0 ; i<BUTTONSCOUNT; i++)
	{
		buttonCurrentStatus[i] = (registerValue >> i) & 0x01;
		if(buttonCurrentStatus[i] && (!buttonLastStatus[i]))
		{
			timerStart[i] = esp_timer_get_time();
		}
		if (!buttonCurrentStatus[i] && buttonLastStatus[i])
		{
			pressDuration[i] = esp_timer_get_time() - timerStart[i];
			if(pressDuration[i] > 1000000)
				{
					ESP_LOGE(TAG,"B%d longe Pressed\n",i+1);
					(*functionPtr)(i+1, LONG_PRESS);
				}
			else
				{
					ESP_LOGE(TAG,"B%d Short Pressed\n",i+1);
					(*functionPtr)(i+1, SHORT_PRESS);
				}
		}
		buttonLastStatus[i] = buttonCurrentStatus[i];
	}
	return 0;
}

void UpdatePOT(void)
{
	Adc_value[ADC1] = adc1_get_raw(ADC1_CHANNEL_0);
	Adc_value[ADC2] = adc1_get_raw(ADC1_CHANNEL_7);
	adc2_get_raw(ADC2_CHANNEL_2, ADC_WIDTH_12Bit, &Adc_value[ADC3]);
	adc2_get_raw(ADC2_CHANNEL_1, ADC_WIDTH_12Bit, &Adc_value[ADC4]);	
	adc2_get_raw(ADC2_CHANNEL_3, ADC_WIDTH_12Bit, &Adc_value[ADC5]);
	adc2_get_raw(ADC2_CHANNEL_6, ADC_WIDTH_12Bit, &Adc_value[ADC6]);	
	adc2_get_raw(ADC2_CHANNEL_4, ADC_WIDTH_12Bit, &Adc_value[ADC7]);
	adc2_get_raw(ADC2_CHANNEL_0, ADC_WIDTH_12Bit, &Adc_value[ADC8]);	
}
 