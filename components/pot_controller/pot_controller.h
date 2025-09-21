#pragma once

#include "driver/adc.h"

class PotController {
public:
    PotController() : value_(0.0f), maxValue_(1.0f), minValue_(0.0f), smoothFactor_(0.1f) {}
    void Init(adc1_channel_t channel, adc_atten_t atten, float max, float min);
    void ProcessControlRate();
    float getValue() const { return value_; }

private:
    adc1_channel_t channel_;
    adc_atten_t atten_;
    float value_;
    float maxValue_;
    float minValue_;
    float smoothFactor_;  // For simple smoothing (replaces ONE_POLE)
    int lastRawValue_;    // For averaging
};

