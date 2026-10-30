/* Isolated board test: ADC is stopped before synthetic DMA words are written.
 * These words only test conversion; none are published as measurement results. */
#include <csignal>
extern "C" {
#include "nullifier.h"
volatile sig_atomic_t nf_stop_requested = 0;
}
#include <rp.h>
#include <rp_hw-profiles.h>
#include <rp_hw_calib.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <stdexcept>
#include <vector>

static void require(bool condition, const char *message) {
    if (!condition) throw std::runtime_error(message);
}

int main() {
    char error[NF_ERROR_SIZE]{};
    int result = 0;
    try {
        nf_config config; nf_defaults(&config);
        config.decimation = 1; config.capture_samples = 65536;
        float *a = nullptr, *b = nullptr;
        require(nf_acquire(&config, &a, &b, error) == 0, error);
        std::free(a); std::free(b);
        uint8_t bits = 0; bool wide = false, signed_adc = false;
        int wide_code = rp_AcqGet16BitMode(&wide);
        rp_HPGetFastADCBits(&bits); rp_HPGetFastADCIsSigned(&signed_adc);
        std::printf("bits=%u signed=%d wide_code=%d wide=%d direct_channels=%u\n",
                    bits, signed_adc, wide_code, wide, nf_acquire_last_timing().direct_channels);
        for (unsigned channel = 0; channel < 2; ++channel) {
            auto ch = static_cast<rp_channel_t>(channel);
            require(rp_AcqAxiEnable(ch, true) == RP_OK, "map stopped DMA buffer");
            bool fpga = false; rp_pinState_t gain; rp_acq_ac_dc_mode_t power;
            float scale = 0, input = 0, offset = 0; uint_gain_calib_t cal{};
            std::printf("ch=%u getters: fpga=%d gain=%d power=%d scale=%d input=%d offset=%d calib=%d\n",
                channel, rp_AcqGetCalibInFPGA(ch, &fpga), rp_AcqGetGain(ch, &gain),
                rp_AcqGetAC_DC(ch, &power), rp_HPGetHWADCFullScale(&scale),
                rp_AcqGetGainV(ch, &input), rp_AcqAxiGetOffset(ch, &offset),
                rp_CalibGetFastADCCalibValueI(channel == 0 ? RP_CH_1_CALIB : RP_CH_2_CALIB,
                                             RP_DC_CALIB, &cal));
            std::printf("calibration: fpga=%d gain=%u base=%u offset=%d full=%g input=%g Voffset=%g\n",
                        fpga, cal.gain, cal.base, cal.offset, scale, input, offset);
        }
        require(nf_acquire_last_timing().direct_channels == 2, "direct read path is not enabled");
        std::vector<float> expected(65536), actual(65536);
        unsigned comparisons = 0;
        for (unsigned channel = 0; channel < 2; ++channel) {
            auto ch = static_cast<rp_channel_t>(channel);
            uint32_t position = 0;
            require(rp_AcqAxiGetWritePointerAtTrig(ch, &position) == RP_OK, "trigger pointer");
            std::vector<std::span<int16_t>> regions;
            require(rp_AcqAxiGetDataRawDirect(ch, position, 65536, &regions) == RP_OK, "direct regions");
            unsigned word = 0;
            for (auto region : regions) for (auto &value : region) value = static_cast<int16_t>(word++);
            require(word == 65536, "complete test code range");
            for (auto gain : {RP_LOW, RP_HIGH}) for (float offset : {0.0f, 0.125f}) {
                require(rp_AcqSetGain(ch, gain) == RP_OK, "set input gain");
                require(rp_AcqAxiSetOffset(ch, offset) == RP_OK, "set display offset");
                for (unsigned shift : {0u, 65499u}) {
                    uint32_t start = (position + shift) % 65536, count = 65536;
                    require(rp_AcqAxiGetDataV(ch, start, &count, expected.data()) == RP_OK,
                            "SDK reference voltage read");
                    bool direct = false;
                    require(nf_read_voltage(channel, start, count, actual.data(), &direct, error) == 0,
                            error);
                    require(direct, "conversion fell back to the SDK");
                    require(!std::memcmp(expected.data(), actual.data(), count * sizeof(float)),
                            "voltage conversion differs from SDK output");
                    comparisons += count;
                }
            }
            rp_AcqSetGain(ch, RP_LOW); rp_AcqAxiSetOffset(ch, 0);
            rp_AcqAxiEnable(ch, false);
        }
        std::printf("PASS: %u bit-exact SDK comparisons across channels, gains, offsets and wrap\n",
                    comparisons);
    } catch (const std::exception &e) {
        std::fprintf(stderr, "%s\n", e.what()); result = 1;
    }
    nf_acquire_shutdown(error);
    return result;
}
