#include "pot_controller.h"
#include "esp_log.h"

#define TAG "PotController"

void PotController::Init(adc1_channel_t channel, adc_atten_t atten, float max, float min) {
    channel_ = channel;
    atten_ = atten;
    maxValue_ = max;
    minValue_ = min;
    lastRawValue_ = 0;

    // Configure ADC
    ESP_ERROR_CHECK(adc1_config_channel_atten(channel_, atten_));
    ESP_LOGI(TAG, "PotController initialized on channel %d with atten %d", channel_, atten_);
}

void PotController::ProcessControlRate() {
    int rawValue = 0;
    ESP_ERROR_CHECK(adc1_get_raw(channel_, &rawValue));
    // Simple moving average filter (replaces ONE_POLE)
    float smoothed = (smoothFactor_ * rawValue + (1.0f - smoothFactor_) * lastRawValue_) / 4095.0f;
    value_ = minValue_ + (maxValue_ - minValue_) * smoothed;
    lastRawValue_ = rawValue;
    ESP_LOGI(TAG, "Pot value: %.2f", value_);
}

