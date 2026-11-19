/* SDK bridge only. Temporal projection, calibration, formula evaluation, variance,
 * JSON parsing and the command loop are compiled as C in the other translation units.
 * Current librp headers contain C++ types, so only this adapter uses C++.
 */
#include "nullifier.h"
#include <rp.h>
#include <rp_hw-profiles.h>
#include <rp_hw_calib.h>

#include <cerrno>
#include <algorithm>
#include <bit>
#include <cmath>
#include <condition_variable>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <csignal>
#include <ctime>
#include <memory>
#include <mutex>
#include <span>
#include <thread>
#include <type_traits>
#include <vector>
#include <fcntl.h>
#include <sys/file.h>
#include <sys/mman.h>
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
static double previous_capture_start = 0;
static std::vector<int16_t> raw_scratch[2];
extern "C" nf_acquire_timing nf_acquire_last_timing() { return last_timing; }

#ifndef NF_FPGA_INFO_PATH
#define NF_FPGA_INFO_PATH "/tmp/loaded_fpga.inf"
#endif
#ifndef NF_LOCK_PATH
#define NF_LOCK_PATH "/var/lock/rtqlm-nullifiers.lock"
#endif
#ifndef NF_HOUSEKEEPING_PATH
#define NF_HOUSEKEEPING_PATH "/dev/mem"
#define NF_HOUSEKEEPING_BASE 0x40000000
#endif

struct TaggedE1 {
    volatile uint32_t *registers = nullptr;
    uint32_t previous = 0;
    bool active = false;

    ~TaggedE1() { restore(); }

    int map(char *error) {
        int fd = open(NF_HOUSEKEEPING_PATH, O_RDWR | O_SYNC | O_CLOEXEC);
        void *page = MAP_FAILED;
        if (fd >= 0) {
            page = mmap(nullptr, 4096, PROT_READ | PROT_WRITE, MAP_SHARED, fd, NF_HOUSEKEEPING_BASE);
            close(fd);
        }
        if (page != MAP_FAILED) {
            registers = static_cast<volatile uint32_t *>(page);
            if (registers[0x110 / 4] == 0x45513101) return 0;
            restore();
        }
        std::snprintf(error, NF_ERROR_SIZE, "RTQLM E1 FPGA signature missing or inaccessible");
        return -1;
    }

    void enable() {
        previous = registers[0x114 / 4];
        active = true;
        registers[0x114 / 4] = (previous & ~3u) | 1u;
    }

    void restore() {
        if (!registers) return;
        if (active) registers[0x114 / 4] = previous;
        munmap(const_cast<uint32_t *>(registers), 4096);
        registers = nullptr;
    }
};

struct RailProjection {
    const nf_processor *processor;
    const nf_raw_channel *raw;
    const nf_tagged_plan *plan;
    unsigned channel;
    int16_t *tile;
    size_t tile_words;
    double *q;

    uint16_t run() const {
        return nf_project_tagged_rail(processor, raw, plan, channel, tile, tile_words, q);
    }
};

class TaggedWorker {
public:
    TaggedWorker() : thread([this] { work(); }) {}
    ~TaggedWorker() {
        {
            std::lock_guard lock(mutex);
            stopping = true;
        }
        changed.notify_one();
        thread.join();
    }

    void submit(const RailProjection &job) {
        std::lock_guard lock(mutex);
        pending = &job;
        changed.notify_one();
    }

    uint16_t join() {
        std::unique_lock lock(mutex);
        changed.wait(lock, [this] { return !pending; });
        return tags;
    }

private:
    void work() {
        std::unique_lock lock(mutex);
        for (;;) {
            changed.wait(lock, [this] { return pending || stopping; });
            if (stopping) return;
            const RailProjection &job = *pending;
            lock.unlock();
            uint16_t result = job.run();
            lock.lock();
            tags = result;
            pending = nullptr;
            changed.notify_one();
        }
    }

    std::mutex mutex;
    std::condition_variable changed;
    const RailProjection *pending = nullptr;
    bool stopping = false;
    uint16_t tags = 0;
    std::thread thread;
};

static std::unique_ptr<TaggedWorker> tagged_worker;

static int project_tagged(nf_processor *processor, const nf_raw_channel raw[2], size_t measurement,
                          size_t tile_words, bool parallel, double *q[2], size_t *modes, char *error) {
    try {
        if (tile_words) {
            tile_words = std::min(tile_words, measurement);
            for (auto &scratch : raw_scratch) {
                if (scratch.size() < tile_words) scratch.resize(tile_words);
            }
        }
        if (parallel && !tagged_worker) tagged_worker = std::make_unique<TaggedWorker>();
    } catch (const std::exception &) {
        std::snprintf(error, NF_ERROR_SIZE, "cannot allocate tagged DMA scratch or start projection worker");
        return -1;
    }
    nf_tagged_plan plan;
    if (nf_validate_tagged_pair(raw, error)) return -1;
    if (nf_prepare_tagged(processor, raw, measurement, &plan, q, error)) return -1;
    RailProjection rails[2];
    for (unsigned ch = 0; ch < 2; ++ch)
        rails[ch] = {processor, &raw[ch], &plan, ch,
                     tile_words ? raw_scratch[ch].data() : nullptr, tile_words, q[ch]};
    if (parallel) tagged_worker->submit(rails[1]);
    const uint16_t a = rails[0].run();
    const uint16_t b = parallel ? tagged_worker->join() : rails[1].run();
    const uint16_t tags = a & b;
    const bool accepted = (tags & 0x8000u) != 0;
    nf_finish_tagged(processor, &plan, q, accepted);
    if (!accepted) {
        std::snprintf(error, NF_ERROR_SIZE, "invalid packed ADC format marker; capture discarded");
        return -1;
    }
    *modes = plan.modes;
    return 0;
}

static double monotonic_seconds() {
    timespec time{};
    clock_gettime(CLOCK_MONOTONIC, &time);
    return static_cast<double>(time.tv_sec) + static_cast<double>(time.tv_nsec) / 1e9;
}

/* Poll on the board, never through SSH. This is a sampled level interlock,
 * not an edge latch: pulses between checks require FPGA-side gating. */
struct AcquisitionGate {
    const nf_config *config;
    double previous_check = 0;
    double next_check = 0;
    bool enabled() const { return config->acquisition_gate_pin[0] != 0; }
    rp_dpin_t pin() const {
        if (!std::strcmp(config->acquisition_gate_pin, "DIO0_P")) return RP_DIO0_P;
        return !std::strcmp(config->acquisition_gate_pin, "DIO3_N") ? RP_DIO3_N : RP_DIO3_P;
    }
    int read(bool &high, char *error) {
        high = true;
        if (!enabled()) return 0;
        double begin = monotonic_seconds();
        const double period = config->acquisition_gate_poll_us * 1e-6;
        if (!next_check) next_check = begin + period;
        else if (begin >= next_check)
            next_check += (std::floor((begin - next_check) / period) + 1) * period;
        if (previous_check) {
            double interval = (begin - previous_check) * 1000;
            last_timing.gate_interval_sum_ms += interval;
            last_timing.gate_intervals++;
            unsigned bucket = static_cast<unsigned>(std::min(interval * 10, 31.0));
            last_timing.gate_interval_histogram[bucket]++;
            last_timing.gate_max_interval_ms = std::max(last_timing.gate_max_interval_ms,
                                                       interval);
        }
        previous_check = begin;
        rp_pinState_t state = RP_LOW;
        int code = rp_DpinGetState(pin(), &state);
        // Inspect the representation without evaluating an invalid C++ enum if
        // an SDK failure writes a value outside its declared LOW/HIGH range.
        std::underlying_type_t<rp_pinState_t> value;
        std::memcpy(&value, &state, sizeof(value));
        last_timing.gate_checks++;
        last_timing.gate_check_ms += (monotonic_seconds() - begin) * 1000;
        if (code != RP_OK || (value != RP_LOW && value != RP_HIGH)) {
            std::snprintf(error, NF_ERROR_SIZE, "acquisition gate %s read failed (%d)",
                          config->acquisition_gate_pin, code); return -1;
        }
        high = value == RP_HIGH;
        return 0;
    }
    void pause(double timeout_deadline) const {
        unsigned us = enabled() ? config->acquisition_gate_poll_us : 1000;
        if (enabled() && previous_check) {
            double next = std::min(next_check, timeout_deadline);
            time_t seconds = static_cast<time_t>(next);
            timespec deadline{seconds, static_cast<long>((next - seconds) * 1e9)};
            clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &deadline, nullptr);
            return;
        }
        timespec delay{static_cast<time_t>(us / 1000000), static_cast<long>(us % 1000000) * 1000};
        nanosleep(&delay, nullptr);
    }
};

// Measure stage boundaries only; no clock reads in the per-sample loop.
struct ReadCheckpoint {
    double *elapsed;
    double started = monotonic_seconds();
    void next(double &stage) {
        double now = monotonic_seconds();
        *elapsed += (now - started) * 1000;
        elapsed = &stage; started = now;
    }
    ~ReadCheckpoint() { *elapsed += (monotonic_seconds() - started) * 1000; }
};

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
    bool checked = false, usable = false;
};
static VoltageTable voltage_tables[2];

static bool read_calibration(unsigned channel, ReadCalibration &parameters, bool uncorrected = false) {
    auto ch = static_cast<rp_channel_t>(channel);
    bool signed_adc = false, wide = false, calibrated_fpga = false;
    rp_pinState_t input_mode;
    rp_acq_ac_dc_mode_t coupling = RP_DC;
    uint_gain_calib_t calibration{};
    (void)rp_AcqGet16BitMode(&wide);
    if (rp_HPGetFastADCBits(&parameters.bits) != RP_OK ||
        rp_HPGetFastADCIsSigned(&signed_adc) != RP_OK || !signed_adc ||
        parameters.bits != 14 || wide ||
        rp_AcqGetCalibInFPGA(ch, &calibrated_fpga) != RP_OK ||
        rp_AcqGetGain(ch, &input_mode) != RP_OK ||
        rp_AcqGetGainV(ch, &parameters.input_gain) != RP_OK ||
        rp_AcqAxiGetOffset(ch, &parameters.voltage_offset) != RP_OK ||
        rp_HPGetHWADCFullScale(&parameters.full_scale) != RP_OK) return false;
    auto rail = channel == 0 ? RP_CH_1_CALIB : RP_CH_2_CALIB;
    if (calibrated_fpga && !uncorrected) {
        calibration.gain = 1; calibration.base = 1; calibration.offset = 0;
    } else {
        (void)rp_AcqGetAC_DC(ch, &coupling);
        auto mode = coupling == RP_AC ? RP_AC_CALIB : RP_DC_CALIB;
        int code = input_mode == RP_LOW ? rp_CalibGetFastADCCalibValueI(rail, mode, &calibration) :
                   rp_CalibGetFastADCCalibValue_1_20I(rail, mode, &calibration);
        if (code != RP_HW_CALIB_OK) return false;
    }
    parameters.gain = calibration.gain; parameters.base = calibration.base;
    parameters.offset = calibration.offset;
    return parameters.base && parameters.base <= INT32_MAX && parameters.gain <= INT32_MAX;
}

static int read_raw_regions(unsigned channel, uint32_t position, unsigned count,
                             nf_raw_channel &raw, char *error,
                             const ReadCalibration *known = nullptr, bool tagged = false,
                             double *preview_scale = nullptr) {
    ReadCheckpoint checkpoint{&last_timing.read_parameters_ms};
    ReadCalibration parameters;
    if (known) parameters = *known;
    else if (!read_calibration(channel, parameters, tagged)) {
        std::snprintf(error, NF_ERROR_SIZE, "raw mode integration requires signed 14-bit ADC calibration");
        return -1;
    }
    double scale = static_cast<double>(parameters.gain) / parameters.base *
                   parameters.full_scale / 8192 * parameters.input_gain;
    if (!(scale > 0) || !std::isfinite(scale)) {
        std::snprintf(error, NF_ERROR_SIZE, "invalid ADC voltage scale"); return -1;
    }
    // Preserve a constant voltage offset in equivalent count units. No per-bin centering.
    raw.zero = parameters.offset - parameters.voltage_offset / scale;
    if (preview_scale) *preview_scale = scale;
    const auto &table = voltage_tables[channel];
    raw.volts = table.usable && table.calibration == parameters ? table.volts.data() : nullptr;
    checkpoint.next(last_timing.read_regions_ms);
    std::vector<std::span<int16_t>> regions;
    if (rp_AcqAxiGetDataRawDirect(static_cast<rp_channel_t>(channel), position, count, &regions) != RP_OK ||
        regions.empty() || regions.size() > 2) {
        std::snprintf(error, NF_ERROR_SIZE, "cannot obtain raw ADC regions"); return -1;
    }
    size_t total = 0;
    for (size_t i = 0; i < regions.size(); ++i) {
        raw.data[i] = regions[i].data(); raw.size[i] = regions[i].size(); total += raw.size[i];
    }
    if (total != count) {
        std::snprintf(error, NF_ERROR_SIZE, "short raw ADC regions"); return -1;
    }
    return 0;
}

static void convert_region(std::span<int16_t> region, const float *volts,
                           unsigned mask, float *output) {
    size_t i = 0;
    // Align wide loads explicitly: a wrapped region can begin at any ADC sample.
    for (; i < region.size() && (reinterpret_cast<uintptr_t>(region.data() + i) & 7); ++i)
        output[i] = volts[static_cast<uint16_t>(region[i]) & mask];
    constexpr bool little = std::endian::native == std::endian::little;
    for (; region.size() - i >= 4; i += 4) {
        uint64_t packed;
        // Fixed eight-byte load into registers; no capture-sized temporary buffer.
        // memcpy avoids aliasing violations and is inlined by the optimized build.
        std::memcpy(&packed, std::assume_aligned<8>(region.data() + i), sizeof(packed));
        output[i]     = volts[static_cast<uint16_t>(packed >> (little ?  0 : 48)) & mask];
        output[i + 1] = volts[static_cast<uint16_t>(packed >> (little ? 16 : 32)) & mask];
        output[i + 2] = volts[static_cast<uint16_t>(packed >> (little ? 32 : 16)) & mask];
        output[i + 3] = volts[static_cast<uint16_t>(packed >> (little ? 48 :  0)) & mask];
    }
    for (; i < region.size(); ++i)
        output[i] = volts[static_cast<uint16_t>(region[i]) & mask];
}

extern "C" int nf_read_voltage(unsigned channel, uint32_t position, unsigned count,
                               float *output, bool *direct, char *error) {
    *direct = false;
    if (channel > 1 || !count || !output) {
        std::snprintf(error, NF_ERROR_SIZE, "invalid direct ADC read request"); return -1;
    }
    auto ch = static_cast<rp_channel_t>(channel);
    ReadCheckpoint checkpoint{&last_timing.read_parameters_ms};
    auto standard = [&]() {
        checkpoint.next(last_timing.read_fallback_ms);
        uint32_t size = count;
        int code = rp_AcqAxiGetDataV(ch, position, &size, output);
        if (code != RP_OK || size != count) {
            std::snprintf(error, NF_ERROR_SIZE, "SDK voltage read failed (%d) or was short", code);
            return -1;
        }
        return 0;
    };
    ReadCalibration parameters;
    if (!read_calibration(channel, parameters)) return standard();
    checkpoint.next(last_timing.read_table_ms);
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
    checkpoint.next(last_timing.read_regions_ms);
    std::vector<std::span<int16_t>> regions;
    if (rp_AcqAxiGetDataRawDirect(ch, position, count, &regions) != RP_OK) return standard();
    checkpoint.next(last_timing.read_convert_ms);
    size_t written = 0;
    unsigned mask = (1u << parameters.bits) - 1;
    // Acquisition is stopped and the DMA mappings remain enabled until this
    // function returns. Convert directly from each region, including circular wrap;
    // only the calibrated float result is materialized, with no raw staging copy.
    for (auto region : regions) {
        if (region.size() > count - written) return standard();
        convert_region(region, table.volts.data(), mask, output + written);
        written += region.size();
    }
    if (written != count) return standard();
    if (!table.checked) {
        checkpoint.next(last_timing.read_verify_ms);
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

static int acquire(const nf_config *config, float **a, float **b, nf_processor *processor,
                    unsigned preview_points, double *q[2], size_t *modes, char *error,
                    nf_raw_channel *export_raw = nullptr) {
    const double begin = monotonic_seconds();
    double wait_begin = begin, read_begin = begin;
    last_timing = {};
    size_t tile_words = 0;
    bool parallel = false;
    const bool tagged = nf_tagged_readout(config->raw_readout, &tile_words, &parallel);
    if (tagged && (!processor || export_raw)) {
        std::snprintf(error, NF_ERROR_SIZE, "tagged E1 requires ARM projection without raw export");
        return -1;
    }
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
    const unsigned measurement = config->measurement_samples ? config->measurement_samples : config->capture_samples;
    unsigned output_count = processor ? std::min(preview_points, tagged ? measurement : config->capture_samples) :
                                        config->capture_samples;
    nf_raw_channel raw[2]{};
    double preview_scale[2]{};
    AcquisitionGate gate{config};
    TaggedE1 tag_control;
    double deadline = 0;
    int code = 0;
#define CHECK(call) do { code = (call); if (code != RP_OK) { \
    std::snprintf(error, NF_ERROR_SIZE, "%s failed (%d)", #call, code); goto cleanup; } } while (0)
    if (tagged && tag_control.map(error)) goto cleanup;
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
    // The SDK's AXI buffers store int16_t words, including the two unused ADC bits.
    required = static_cast<uint64_t>(config->capture_samples) * sizeof(int16_t);
    if (!half || address % 4096 || required > half ||
        static_cast<uint64_t>(address) + 2 * half > UINT32_MAX) {
        std::snprintf(error, NF_ERROR_SIZE, "reserved DMA region cannot hold both aligned capture buffers");
        goto cleanup;
    }
    *a = *b = nullptr;
    if (output_count) {
        *a = static_cast<float *>(std::malloc(output_count * sizeof(float)));
        *b = static_cast<float *>(std::malloc(output_count * sizeof(float)));
    }
    if (output_count && (!*a || !*b)) {
        std::snprintf(error, NF_ERROR_SIZE, "cannot allocate capture buffers"); goto cleanup;
    }
    CHECK(rp_AcqStop());
    CHECK(rp_AcqReset());
    CHECK(rp_AcqSetTriggerSrc(RP_TRIG_SRC_DISABLED));
    CHECK(rp_AcqAxiEnable(RP_CH_1, false)); CHECK(rp_AcqAxiEnable(RP_CH_2, false));
    if (tagged) tag_control.enable();
    CHECK(rp_AcqAxiSetDecimationFactor(config->decimation));
    for (unsigned i = 0; i < 2; ++i) {
        CHECK(rp_AcqAxiSetBufferSamples(channels[i], address + static_cast<uint32_t>(i * half), config->capture_samples));
        CHECK(rp_AcqAxiSetTriggerDelay(channels[i], static_cast<int32_t>(config->capture_samples)));
        CHECK(rp_AcqAxiEnable(channels[i], true)); enabled[i] = true;
    }
    if (config->trigger[0] == 'a') CHECK(rp_AcqSetTriggerLevel(RP_T_CH_1, static_cast<float>(config->trigger_level)));
    if (config->trigger[0] == 'b') CHECK(rp_AcqSetTriggerLevel(RP_T_CH_2, static_cast<float>(config->trigger_level)));
    if (tagged) CHECK(rp_DpinSetDirection(RP_DIO0_P, RP_IN));
    if (gate.enabled() && !tagged) {
        CHECK(rp_DpinSetDirection(gate.pin(), RP_IN));
        const double gate_begin = monotonic_seconds();
        deadline = gate_begin + config->timeout_seconds;
        for (;;) {
            bool high;
            int gate_result = gate.read(high, error);
            last_timing.gate_wait_ms = (monotonic_seconds() - gate_begin) * 1000;
            if (gate_result) goto cleanup;
            if (nf_stop_requested || monotonic_seconds() >= deadline) {
                std::snprintf(error, NF_ERROR_SIZE, "acquisition gate %s cancelled or HIGH wait timeout",
                              config->acquisition_gate_pin); goto cleanup;
            }
            if (high) break;
            gate.pause(deadline);
        }
    }
    if (nf_stop_requested) {
        std::snprintf(error, NF_ERROR_SIZE, "capture cancelled before start"); goto cleanup;
    }
    CHECK(rp_AcqStart()); started = true;
    {
        double now = monotonic_seconds();
        last_timing.start_interval_ms = previous_capture_start ? (now - previous_capture_start) * 1000 : 0;
        previous_capture_start = now;
        last_timing.sample_duration_ms = static_cast<double>(config->capture_samples) * config->decimation / rate * 1000;
    }
    CHECK(rp_AcqSetTriggerSrc(trigger_source(config->trigger)));
    wait_begin = monotonic_seconds();
    last_timing.setup_ms = (wait_begin - begin) * 1000 - last_timing.gate_wait_ms;
    deadline = monotonic_seconds() + config->timeout_seconds;
    for (;;) {
        if (!tagged) {
            bool high;
            if (gate.read(high, error)) goto cleanup;
            if (!high) {
                std::snprintf(error, NF_ERROR_SIZE, "acquisition gate %s went LOW; capture discarded",
                              config->acquisition_gate_pin); goto cleanup;
            }
        }
        if (nf_stop_requested || monotonic_seconds() >= deadline) {
            std::snprintf(error, NF_ERROR_SIZE, "capture cancelled or trigger/fill timeout"); goto cleanup;
        }
        bool filled[2] = {false, false};
        for (unsigned i = 0; i < 2; ++i) CHECK(rp_AcqAxiGetBufferFillState(channels[i], &filled[i]));
        if (filled[0] && filled[1]) break;
        if (tagged) {
            const timespec pause{0, 500000};
            nanosleep(&pause, nullptr);
        } else gate.pause(deadline);
    }
    CHECK(rp_AcqStop()); started = false;
    if (!tagged) {
        bool high;
        if (gate.read(high, error)) goto cleanup;
        if (!high) {
            std::snprintf(error, NF_ERROR_SIZE, "acquisition gate %s LOW at capture completion; capture discarded",
                          config->acquisition_gate_pin); goto cleanup;
        }
    }
    if (nf_stop_requested) {
        std::snprintf(error, NF_ERROR_SIZE, "capture cancelled at completion"); goto cleanup;
    }
    read_begin = monotonic_seconds();
    last_timing.wait_ms = (read_begin - wait_begin) * 1000;
    for (unsigned i = 0; i < 2; ++i) CHECK(rp_AcqAxiGetWritePointerAtTrig(channels[i], &positions[i]));
    for (unsigned i = 0; i < 2; ++i) last_timing.trigger_positions[i] = positions[i];
    if (tagged && positions[0] != positions[1]) {
        std::snprintf(error, NF_ERROR_SIZE, "packed ADC trigger positions disagree; capture discarded");
        goto cleanup;
    }
    last_timing.read_pointers_ms = (monotonic_seconds() - read_begin) * 1000;
    for (unsigned i = 0; i < 2; ++i) {
        double channel_begin = monotonic_seconds();
        bool direct = false;
        if (!tagged && output_count && nf_read_voltage(i, positions[i], output_count, i ? *b : *a, &direct, error))
            goto cleanup;
        if (processor) {
            if (!output_count && config->reject_threshold > 0) {
                float probe;
                if (nf_read_voltage(i, positions[i], 1, &probe, &direct, error)) goto cleanup;
            }
            // A successful direct preview already fetched these exact settings
            // during this stopped capture. Avoid a second set of SDK queries.
            const ReadCalibration *known = direct ? &voltage_tables[i].calibration : nullptr;
            if (read_raw_regions(i, positions[i], config->capture_samples, raw[i], error, known, tagged,
                                 tagged && output_count ? &preview_scale[i] : nullptr)) goto cleanup;
            direct = true;
        }
        last_timing.channel_read_ms[i] = (monotonic_seconds() - channel_begin) * 1000;
        last_timing.direct_channels += direct;
    }
    if (tagged) {
        double projection_begin = monotonic_seconds();
        int projected = project_tagged(processor, raw, measurement, tile_words, parallel, q, modes, error);
        last_timing.raw_projection_ms = (monotonic_seconds() - projection_begin) * 1000;
        if (projected) goto cleanup;
        if (output_count) {
            ReadCheckpoint preview{&last_timing.read_convert_ms};
            const unsigned stride = measurement / output_count;
            last_timing.preview_stride = stride;
            for (unsigned ch = 0; ch < 2; ++ch) {
                float *out = ch ? *b : *a;
                for (unsigned i = 0; i < output_count; ++i) {
                    size_t at = i * stride;
                    uint16_t word = at < raw[ch].size[0] ? raw[ch].data[0][at] :
                                    raw[ch].data[1][at - raw[ch].size[0]];
                    int value = static_cast<int>(word & 8191) - static_cast<int>(word & 8192);
                    out[i] = static_cast<float>((value - raw[ch].zero) * preview_scale[ch]);
                }
            }
        }
    } else if (processor) {
        bool copy = export_raw || !std::strcmp(config->raw_readout, "ram_copy");
        if (copy) {
            const double copy_begin = monotonic_seconds();
            try {
                for (unsigned ch = 0; ch < 2; ++ch) {
                    auto &buffer = raw_scratch[ch];
                    buffer.resize(config->capture_samples);
                    size_t at = 0;
                    for (unsigned region = 0; region < 2; ++region) if (raw[ch].size[region]) {
                        std::memcpy(buffer.data() + at, raw[ch].data[region], raw[ch].size[region] * sizeof(int16_t));
                        at += raw[ch].size[region];
                    }
                    raw[ch].data[0] = buffer.data(); raw[ch].size[0] = config->capture_samples;
                    raw[ch].data[1] = nullptr; raw[ch].size[1] = 0;
                    last_timing.raw_copy_bytes += at * sizeof(int16_t);
                }
            } catch (const std::bad_alloc &) {
                std::snprintf(error, NF_ERROR_SIZE, "cannot allocate raw RAM buffers"); goto cleanup;
            }
            last_timing.raw_copy_ms = (monotonic_seconds() - copy_begin) * 1000;
        }
        double projection_begin = monotonic_seconds();
        if (export_raw) {
            export_raw[0] = raw[0]; export_raw[1] = raw[1];
        } else {
        nf_raw_channel measured[2] = {raw[0], raw[1]};
        for (auto &channel : measured) {
            channel.size[0] = std::min(channel.size[0], static_cast<size_t>(measurement));
            channel.size[1] = measurement - channel.size[0];
        }
        int projected = copy ? nf_project_raw(processor, measured, measurement, q, modes, error) :
                               nf_project_raw_dma(processor, measured, measurement, q, modes, error);
        if (projected) goto cleanup;
        }
        last_timing.raw_projection_ms = (monotonic_seconds() - projection_begin) * 1000;
    }
    result = 0;
    last_timing.read_ms = (monotonic_seconds() - read_begin) * 1000;
cleanup:
    const double cleanup_begin = monotonic_seconds();
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
    tag_control.restore();
    close(lock);
    last_timing.cleanup_ms = (monotonic_seconds() - cleanup_begin) * 1000;
    last_timing.total_ms = (monotonic_seconds() - begin) * 1000;
    return result;
#undef CHECK
}

extern "C" int nf_acquire(const nf_config *config, float **a, float **b, char *error) {
    return acquire(config, a, b, nullptr, 0, nullptr, nullptr, error);
}

/* Borrowed RAM views remain valid until the next acquisition or shutdown.
 * DMA is stopped and unmapped before returning; transmission cannot overwrite them. */
extern "C" int nf_acquire_raw(nf_processor *p, nf_raw_channel raw[2], char *error) {
    float *a = nullptr, *b = nullptr;
    int result = acquire(&p->config, &a, &b, p, 0, nullptr, nullptr, error, raw);
    std::free(a); std::free(b);
    return result;
}

extern "C" int nf_acquire_raw_preview(nf_processor *p, nf_raw_channel raw[2],
                                       unsigned points, float **a, float **b, char *error) {
    return acquire(&p->config, a, b, p, points, nullptr, nullptr, error, raw);
}

extern "C" int nf_acquire_modes(nf_processor *p, unsigned preview_points, float **a, float **b,
                                 double *q[2], size_t *modes, char *error) {
    q[0] = q[1] = nullptr; *modes = 0;
    return acquire(&p->config, a, b, p, preview_points, q, modes, error);
}

extern "C" int nf_acquire_shutdown(char *error) {
    if (!sdk_initialized) return 0;
    // Acquisition-only clients do not own the pre-existing generator outputs.
    if (generator_initialized) {
        rp_GenOutDisable(RP_CH_1); rp_GenOutDisable(RP_CH_2);
    }
    int code = rp_Release();
    sdk_initialized = false; factory_calibrated = false; generator_initialized = false;
    for (auto &table : voltage_tables) table = {};
    tagged_worker.reset();
    for (auto &buffer : raw_scratch) std::vector<int16_t>().swap(buffer);
    previous_capture_start = 0;
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
