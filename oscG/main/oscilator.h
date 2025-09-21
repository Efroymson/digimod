#pragma once
#include <stdint.h>
#include "stmlib/stmlib.h"  // Assuming from your Plaits reuse; else use stdint

namespace dms {  // Digital Modular Synth namespace

class Oscillator {
public:
    Oscillator() : phase_(0.0f), frequency_(440.0f), shape_(0.0f) {}
    ~Oscillator() {}

    void Init(int sample_rate) {
        sample_rate_ = sample_rate;
        phase_inc_ = 0.0f;
    }

    inline void SetFrequency(float freq) { frequency_ = freq; UpdatePhaseInc(); }
    inline void SetShape(float shape) { shape_ = stmlib::CONSTRAIN(shape, 0.0f, 1.0f); }

    void Render(int16_t* buffer, size_t size) {
        for (size_t i = 0; i < size; ++i) {
            // Basic sine + square morph (0=sine, 1=square)
            float phase = phase_;
            float sine = sinf(2.0f * M_PI * phase);
            float square = (phase < 0.5f) ? 1.0f : -1.0f;
            float sample = (1.0f - shape_) * sine + shape_ * square;

            // Quantize to 24-bit signed (-1 to 1 -> -8388608 to 8388607)
            buffer[i] = (int16_t)(sample * 8388607.0f);  // Scale; extend to 24-bit if needed

            phase_ += phase_inc_;
            if (phase_ >= 1.0f) phase_ -= 1.0f;
        }
    }

private:
    void UpdatePhaseInc() {
        phase_inc_ = frequency_ / sample_rate_;
        stmlib::CONSTRAIN(phase_inc_, 0.0f, 1.0f);
    }

    float phase_;
    float phase_inc_;
    float frequency_;
    float shape_;
    int sample_rate_;
};

}  // namespace dms
