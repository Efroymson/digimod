#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/gpio.h"
#include "daisysp.h"

#define SAMPLE_RATE 48000
#define BLOCK_SIZE 96

static daisysp::Oscillator osc;

extern "C" void app_main(void) {
    osc.Init(SAMPLE_RATE);
    osc.SetWaveform(daisysp::Oscillator::WAVE_SAW);
    osc.SetFreq(440.0f); // A4 note

    while (1) {
        for (int i = 0; i < BLOCK_SIZE; ++i) {
            float sample = osc.Process();
            // Placeholder: Output to GPIO or UDP (implement later)
            printf("Sample: %f\n", sample);
        }
        vTaskDelay(1 / portTICK_PERIOD_MS); // Avoid watchdog
    }
}
