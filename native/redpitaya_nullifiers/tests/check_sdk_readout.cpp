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
#include <cmath>
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
        require(nf_acquire_last_timing().read_verify_ms > 0, "cold read did not time SDK verification");
        require(nf_acquire(&config, &a, &b, error) == 0, error);
        std::free(a); std::free(b);
        auto timing = nf_acquire_last_timing();
        require(timing.read_verify_ms == 0, "warm read retained verification timing");
        require(timing.read_convert_ms > 0 && timing.read_parameters_ms > 0,
                "read checkpoints did not record elapsed time");
        double stages = timing.read_pointers_ms + timing.read_parameters_ms + timing.read_table_ms +
                        timing.read_regions_ms + timing.read_convert_ms + timing.read_verify_ms +
                        timing.read_fallback_ms;
        require(stages <= timing.read_ms, "read stages exceed total read timing");
        require(timing.channel_read_ms[0] > 0 && timing.channel_read_ms[1] > 0 &&
                timing.channel_read_ms[0] + timing.channel_read_ms[1] <= timing.read_ms,
                "channel checkpoints exceed total read timing");
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
                for (unsigned shift : {0u, 65499u}) for (uint32_t requested : {1u, 38u, 39u, 1024u, 65536u}) {
                    uint32_t start = (position + shift) % 65536, count = requested;
                    require(rp_AcqAxiGetDataV(ch, start, &count, expected.data()) == RP_OK,
                            "SDK reference voltage read");
                    require(count == requested, "short SDK reference read");
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
        config.guard_seconds = 0; config.end_guard = 0; config.b_delay = 0;
        config.min_samples = 2;
        const char *shapes[] = {"identity", "gaussian_linear", "lab_gaussian_linear_cosine"};
        for (const char *shape : shapes) {
            std::strcpy(config.shape, shape);
            auto raw = new nf_processor{}; auto reference = new nf_processor{};
            require(!nf_configure(raw, &config, error) && !nf_configure(reference, &config, error), error);
            double *q[2]{}; size_t modes = 0;
            a = b = nullptr;
            require(!nf_acquire_modes(raw, 1024, &a, &b, q, &modes, error), error);
            auto raw_timing = nf_acquire_last_timing();
            std::vector<float> captured[2] = {std::vector<float>(config.capture_samples),
                                             std::vector<float>(config.capture_samples)};
            double scales[2]{};
            for (unsigned channel = 0; channel < 2; ++channel) {
                auto ch = static_cast<rp_channel_t>(channel);
                require(rp_AcqAxiEnable(ch, true) == RP_OK, "remap stopped raw capture");
                // Enabling a mapping changes the SDK pointer state. Use the original
                // capture position saved before disable/unmap, as the live reader does.
                uint32_t position = raw_timing.trigger_positions[channel], count = config.capture_samples;
                require(rp_AcqAxiGetDataV(ch, position, &count, captured[channel].data()) == RP_OK &&
                        count == config.capture_samples, "raw capture SDK reference");
                if (std::memcmp(channel ? b : a, captured[channel].data(), 1024 * sizeof(float))) {
                    std::fprintf(stderr, "preview mismatch ch=%u position=%u count=%u\n",
                                 channel, position, count);
                    for (unsigned j = 0; j < 8; ++j) std::fprintf(stderr, "%u: %.9g vs %.9g\n", j,
                        (channel ? b : a)[j], captured[channel][j]);
                }
                require(!std::memcmp(channel ? b : a, captured[channel].data(), 1024 * sizeof(float)),
                        "raw path changed the voltage waveform preview");
                bool fpga = false; float full = 0, input_gain = 0;
                require(rp_AcqGetCalibInFPGA(ch, &fpga) == RP_OK && fpga,
                        "this board parity oracle requires FPGA-calibrated counts");
                require(rp_HPGetHWADCFullScale(&full) == RP_OK && rp_AcqGetGainV(ch, &input_gain) == RP_OK,
                        "raw capture voltage scale");
                scales[channel] = (double)full / 8192 * input_gain;
                require(rp_AcqAxiEnable(ch, false) == RP_OK, "unmap raw reference");
            }
            require(!nf_process(reference, captured[0].data(), captured[1].data(), config.capture_samples, error), error);
            require(modes == reference->quadrature_count && raw->raw_counts, "raw mode count or units");
            for (unsigned channel = 0; channel < 2; ++channel) for (size_t k = 0; k < modes; ++k)
                require(std::fabs(q[channel][k] - reference->quadratures[channel][k] / scales[channel]) < 1e-9,
                        "raw captured modes differ from the SDK voltage oracle");
            std::printf("PASS: %zu real %s modes per channel match SDK voltage projections; preview bit-exact\n", modes, shape);
            std::free(a); std::free(b); std::free(q[0]); std::free(q[1]);
            nf_destroy(raw); nf_destroy(reference); delete raw; delete reference;
        }
    } catch (const std::exception &e) {
        std::fprintf(stderr, "%s\n", e.what()); result = 1;
    }
    nf_acquire_shutdown(error);
    return result;
}
