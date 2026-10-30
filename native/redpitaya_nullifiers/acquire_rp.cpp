/* SDK bridge only. Temporal projection, calibration, formula evaluation, variance,
 * JSON parsing and the command loop are compiled as C in the other translation units.
 * Current librp headers contain C++ types, so only this adapter uses C++.
 */
#include "nullifier.h"
#include <rp.h>
#include <rp_hw-profiles.h>
#include <rp_hw_calib.h>

#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <csignal>
#include <ctime>
#include <span>
#include <vector>
#include <fcntl.h>
#include <sys/file.h>
#include <unistd.h>

extern "C" volatile sig_atomic_t nf_stop_requested;

/* Keep the SDK open for the command session. The installed SDK leaves closed
 * channel UIO descriptors cached on release, so release/reinitialization between
 * captures can reuse stale descriptors. DMA itself is stopped/disabled per shot.
 */
static bool sdk_initialized = false;
static bool factory_calibrated = false;
static bool generator_initialized = false;
static nf_acquire_timing last_timing{};
extern "C" nf_acquire_timing nf_acquire_last_timing() { return last_timing; }

#ifndef NF_FPGA_INFO_PATH
#define NF_FPGA_INFO_PATH "/tmp/loaded_fpga.inf"
#endif
#ifndef NF_LOCK_PATH
#define NF_LOCK_PATH "/var/lock/rtqlm-nullifiers.lock"
#endif

static double monotonic_seconds() {
    timespec time{};
    clock_gettime(CLOCK_MONOTONIC, &time);
    return static_cast<double>(time.tv_sec) + static_cast<double>(time.tv_nsec) / 1e9;
}

static rp_acq_trig_src_t trigger_source(const char *name) {
    if (!std::strcmp(name, "external_rising")) return RP_TRIG_SRC_EXT_PE;
    if (!std::strcmp(name, "external_falling")) return RP_TRIG_SRC_EXT_NE;
    if (!std::strcmp(name, "a_rising")) return RP_TRIG_SRC_CHA_PE;
    if (!std::strcmp(name, "a_falling")) return RP_TRIG_SRC_CHA_NE;
    if (!std::strcmp(name, "b_rising")) return RP_TRIG_SRC_CHB_PE;
    if (!std::strcmp(name, "b_falling")) return RP_TRIG_SRC_CHB_NE;
    return RP_TRIG_SRC_NOW;
}

struct ReadCalibration {
    uint8_t bits = 0;
    uint32_t gain = 0, base = 0;
    int32_t offset = 0;
    float full_scale = 0, input_gain = 0, voltage_offset = 0;
    bool operator==(const ReadCalibration &) const = default;
};
struct VoltageTable {
    ReadCalibration calibration{};
    std::vector<float> volts;
    std::vector<uint16_t> words;
    bool checked = false, usable = false;
};
static VoltageTable voltage_tables[2];

extern "C" int nf_read_voltage(unsigned channel, uint32_t position, unsigned count,
                               float *output, bool *direct, char *error) {
    *direct = false;
    if (channel > 1 || !count || !output) {
        std::snprintf(error, NF_ERROR_SIZE, "invalid direct ADC read request"); return -1;
    }
    auto ch = static_cast<rp_channel_t>(channel);
    auto standard = [&]() {
        uint32_t size = count;
        int code = rp_AcqAxiGetDataV(ch, position, &size, output);
        if (code != RP_OK || size != count) {
            std::snprintf(error, NF_ERROR_SIZE, "SDK voltage read failed (%d) or was short", code);
            return -1;
        }
        return 0;
    };
    ReadCalibration parameters;
    bool signed_adc = false, wide = false, calibrated_fpga = false;
    rp_pinState_t input_mode;
    rp_acq_ac_dc_mode_t coupling = RP_DC;
    uint_gain_calib_t calibration{};
    // Like the SDK voltage routine, retain false when this optional mode is unsupported.
    (void)rp_AcqGet16BitMode(&wide);
    if (rp_HPGetFastADCBits(&parameters.bits) != RP_OK ||
        rp_HPGetFastADCIsSigned(&signed_adc) != RP_OK ||
        parameters.bits != 14 || wide ||
        rp_AcqGetCalibInFPGA(ch, &calibrated_fpga) != RP_OK ||
        rp_AcqGetGain(ch, &input_mode) != RP_OK ||
        rp_AcqGetGainV(ch, &parameters.input_gain) != RP_OK ||
        rp_AcqAxiGetOffset(ch, &parameters.voltage_offset) != RP_OK ||
        rp_HPGetHWADCFullScale(&parameters.full_scale) != RP_OK) return standard();
    auto rail = channel == 0 ? RP_CH_1_CALIB : RP_CH_2_CALIB;
    auto mode = coupling == RP_AC ? RP_AC_CALIB : RP_DC_CALIB;
    if (calibrated_fpga) {
        calibration.gain = 1; calibration.base = 1; calibration.offset = 0;
    } else {
        // Fixed-DC boards can report RP_NOTS without providing a coupling value.
        (void)rp_AcqGetAC_DC(ch, &coupling);
        int code = input_mode == RP_LOW ? rp_CalibGetFastADCCalibValueI(rail, mode, &calibration) :
                   rp_CalibGetFastADCCalibValue_1_20I(rail, mode, &calibration);
        if (code != RP_HW_CALIB_OK) return standard();
    }
    parameters.gain = calibration.gain; parameters.base = calibration.base;
    parameters.offset = calibration.offset;
    if (!parameters.base || parameters.base > INT32_MAX || parameters.gain > INT32_MAX)
        return standard();
    auto &table = voltage_tables[channel];
    if (!(table.calibration == parameters) || table.volts.empty()) {
        table.calibration = parameters; table.checked = false; table.usable = true;
        unsigned codes = 1u << parameters.bits, half_codes = codes / 2;
        table.volts.resize(codes);
        for (unsigned word = 0; word < codes; ++word) {
            int32_t signed_word = word < half_codes ? static_cast<int32_t>(word) :
                                  static_cast<int32_t>(word) - static_cast<int32_t>(codes);
            int64_t product = static_cast<int64_t>(parameters.gain) *
                              (static_cast<int64_t>(signed_word) - parameters.offset);
            if (product < INT32_MIN || product > INT32_MAX) { table.usable = false; break; }
            int32_t corrected = static_cast<int32_t>(product) / static_cast<int32_t>(parameters.base);
            float value = static_cast<float>(corrected) * parameters.full_scale /
                          static_cast<float>(half_codes);
            table.volts[word] = value * parameters.input_gain + parameters.voltage_offset;
        }
    }
    if (!table.usable) return standard();
    std::vector<std::span<int16_t>> regions;
    if (rp_AcqAxiGetDataRawDirect(ch, position, count, &regions) != RP_OK) return standard();
    table.words.resize(count);
    size_t written = 0;
    unsigned mask = (1u << parameters.bits) - 1;
    for (auto region : regions) {
        if (region.size() > count - written) return standard();
        std::memcpy(table.words.data() + written, region.data(), region.size_bytes());
        written += region.size();
    }
    if (written != count) return standard();
    for (unsigned i = 0; i < count; ++i) output[i] = table.volts[table.words[i] & mask];
    if (!table.checked) {
        std::vector<float> reference(count);
        uint32_t size = count;
        if (rp_AcqAxiGetDataV(ch, position, &size, reference.data()) != RP_OK || size != count)
            return standard();
        table.checked = true;
        if (std::memcmp(output, reference.data(), count * sizeof(float))) {
            table.usable = false;
            std::memcpy(output, reference.data(), count * sizeof(float));
            return 0;
        }
    }
    *direct = true;
    return 0;
}

extern "C" int nf_acquire(const nf_config *config, float **a, float **b, char *error) {
    const double begin = monotonic_seconds();
    double wait_begin = begin, read_begin = begin;
    last_timing = {};
    int result = -1;
    int lock = open(NF_LOCK_PATH, O_CREAT | O_RDWR, 0600);
    if (lock < 0 || flock(lock, LOCK_EX | LOCK_NB)) {
        std::snprintf(error, NF_ERROR_SIZE, "cannot obtain exclusive nullifier acquisition lock");
        if (lock >= 0) close(lock);
        return -1;
    }
    /* Refuse an incompatible image rather than writing scope registers while
     * stream_app owns that address space. Image selection belongs to deployment.
     */
    char image[128]{};
    FILE *info = std::fopen(NF_FPGA_INFO_PATH, "r");
    if (info) {
        if (std::fscanf(info, "%127s", image) != 1) image[0] = 0;
        std::fclose(info);
    }
    if (std::strcmp(image, "v0.94")) {
        std::snprintf(error, NF_ERROR_SIZE, "load the v0.94 acquisition FPGA before capture (current: %.80s)", image);
        close(lock); return -1;
    }
    bool started = false, enabled[2] = {false, false};
    const rp_channel_t channels[2] = {RP_CH_1, RP_CH_2};
    uint32_t address = 0, bytes = 0, rate = 0;
    uint32_t positions[2] = {0, 0};
    uint64_t half = 0, required = 0;
    double deadline = 0;
    int code = 0;
#define CHECK(call) do { code = (call); if (code != RP_OK) { \
    std::snprintf(error, NF_ERROR_SIZE, "%s failed (%d)", #call, code); goto cleanup; } } while (0)
    if (!sdk_initialized) {
        CHECK(rp_InitReset(false)); sdk_initialized = true;
    }
    if (!factory_calibrated) {
        CHECK(rp_CalibInit()); factory_calibrated = true;
    }
    /* This first implementation targets the two 125 MS/s rails, never guessing
     * the mode clock of a different board model.
     */
    CHECK(rp_HPGetBaseFastADCSpeedHz(&rate));
    if (rate != 125000000) {
        std::snprintf(error, NF_ERROR_SIZE, "this processor currently requires a 125 MS/s board");
        goto cleanup;
    }
    CHECK(rp_AcqAxiGetMemoryRegion(&address, &bytes));
    half = (static_cast<uint64_t>(bytes) / 2) & ~uint64_t{4095};
    required = static_cast<uint64_t>(config->capture_samples) * 4;
    if (!half || address % 4096 || required > half ||
        static_cast<uint64_t>(address) + 2 * half > UINT32_MAX) {
        std::snprintf(error, NF_ERROR_SIZE, "reserved DMA region cannot hold both aligned capture buffers");
        goto cleanup;
    }
    *a = static_cast<float *>(std::calloc(config->capture_samples, sizeof(float)));
    *b = static_cast<float *>(std::calloc(config->capture_samples, sizeof(float)));
    if (!*a || !*b) {
        std::snprintf(error, NF_ERROR_SIZE, "cannot allocate capture buffers"); goto cleanup;
    }
    CHECK(rp_AcqStop());
    CHECK(rp_AcqReset());
    CHECK(rp_AcqSetTriggerSrc(RP_TRIG_SRC_DISABLED));
    CHECK(rp_AcqAxiEnable(RP_CH_1, false)); CHECK(rp_AcqAxiEnable(RP_CH_2, false));
    CHECK(rp_AcqAxiSetDecimationFactor(config->decimation));
    for (unsigned i = 0; i < 2; ++i) {
        CHECK(rp_AcqAxiSetBufferSamples(channels[i], address + static_cast<uint32_t>(i * half), config->capture_samples));
        CHECK(rp_AcqAxiSetTriggerDelay(channels[i], static_cast<int32_t>(config->capture_samples)));
        CHECK(rp_AcqAxiEnable(channels[i], true)); enabled[i] = true;
    }
    if (config->trigger[0] == 'a') CHECK(rp_AcqSetTriggerLevel(RP_T_CH_1, static_cast<float>(config->trigger_level)));
    if (config->trigger[0] == 'b') CHECK(rp_AcqSetTriggerLevel(RP_T_CH_2, static_cast<float>(config->trigger_level)));
    CHECK(rp_AcqStart()); started = true;
    CHECK(rp_AcqSetTriggerSrc(trigger_source(config->trigger)));
    wait_begin = monotonic_seconds();
    last_timing.setup_ms = (wait_begin - begin) * 1000;
    deadline = monotonic_seconds() + config->timeout_seconds;
    for (;;) {
        bool filled[2] = {false, false};
        for (unsigned i = 0; i < 2; ++i) CHECK(rp_AcqAxiGetBufferFillState(channels[i], &filled[i]));
        if (filled[0] && filled[1]) break;
        if (nf_stop_requested || monotonic_seconds() >= deadline) {
            std::snprintf(error, NF_ERROR_SIZE, "capture cancelled or trigger/fill timeout"); goto cleanup;
        }
        timespec pause{0, 1000000}; nanosleep(&pause, nullptr);
    }
    CHECK(rp_AcqStop()); started = false;
    read_begin = monotonic_seconds();
    last_timing.wait_ms = (read_begin - wait_begin) * 1000;
    for (unsigned i = 0; i < 2; ++i) CHECK(rp_AcqAxiGetWritePointerAtTrig(channels[i], &positions[i]));
    for (unsigned i = 0; i < 2; ++i) {
        bool direct = false;
        if (nf_read_voltage(i, positions[i], config->capture_samples, i ? *b : *a, &direct, error))
            goto cleanup;
        last_timing.direct_channels += direct;
    }
    result = 0;
    last_timing.read_ms = (monotonic_seconds() - read_begin) * 1000;
cleanup:
    if (sdk_initialized) {
        int cleanup_code = RP_OK;
        if (started) cleanup_code = rp_AcqStop();
        for (unsigned i = 0; i < 2; ++i) if (enabled[i]) {
            int disabled = rp_AcqAxiEnable(channels[i], false);
            if (disabled != RP_OK) cleanup_code = disabled;
        }
        if (cleanup_code != RP_OK && result == 0) {
            std::snprintf(error, NF_ERROR_SIZE, "acquisition cleanup failed (%d)", cleanup_code);
            result = -1;
        }
    }
    close(lock);
    return result;
#undef CHECK
}

extern "C" int nf_acquire_shutdown(char *error) {
    if (!sdk_initialized) return 0;
    rp_GenOutDisable(RP_CH_1); rp_GenOutDisable(RP_CH_2);
    int code = rp_Release();
    sdk_initialized = false; factory_calibrated = false; generator_initialized = false;
    for (auto &table : voltage_tables) table = {};
    if (code != RP_OK) {
        std::snprintf(error, NF_ERROR_SIZE, "SDK shutdown failed (%d)", code);
        return -1;
    }
    return 0;
}

extern "C" int nf_set_output(unsigned channel, bool enabled, unsigned waveform, double frequency,
                             double amplitude, double offset, double phase, char *error) {
    if (channel < 1 || channel > 2 || (waveform > 5 && waveform != 10)) {
        std::snprintf(error, NF_ERROR_SIZE, "unsupported SDK output channel or waveform"); return -1;
    }
    if (!sdk_initialized) {
        int initialized = rp_InitReset(false);
        if (initialized != RP_OK) {
            std::snprintf(error, NF_ERROR_SIZE, "generator SDK initialization failed (%d)", initialized);
            return -1;
        }
        sdk_initialized = true;
    }
    rp_channel_t ch = channel == 1 ? RP_CH_1 : RP_CH_2;
    if (!generator_initialized) {
        int reset = rp_GenReset();
        if (reset != RP_OK) {
            std::snprintf(error, NF_ERROR_SIZE, "generator reset failed (%d)", reset); return -1;
        }
        generator_initialized = true;
    }
    int code = rp_GenOutDisable(ch);
    if (!code && enabled) code = rp_GenMode(ch, RP_GEN_MODE_CONTINUOUS);
    if (!code && enabled) code = rp_GenWaveform(ch, static_cast<rp_waveform_t>(waveform));
    if (!code && enabled) code = rp_GenFreq(ch, static_cast<float>(frequency));
    if (!code && enabled) code = rp_GenAmp(ch, static_cast<float>(amplitude));
    if (!code && enabled) code = rp_GenOffset(ch, static_cast<float>(offset));
    if (!code && enabled) code = rp_GenPhase(ch, static_cast<float>(phase));
    if (!code && enabled) code = rp_GenOutEnable(ch);
    if (!code && enabled) code = rp_GenTriggerOnly(ch);
    if (code) {
        rp_GenOutDisable(ch);
        std::snprintf(error, NF_ERROR_SIZE, "generator configuration failed (%d)", code); return -1;
    }
    return 0;
}
