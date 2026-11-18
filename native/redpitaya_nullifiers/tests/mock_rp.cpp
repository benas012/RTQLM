/* Test double compiled against the same official headers as the real adapter. */
#include <rp.h>
#include <rp_hw-profiles.h>
#include <rp_hw_calib.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <span>
#include <vector>

static std::vector<int16_t> dma[2];
static unsigned gate_reads = 0, fill_reads = 0;
static unsigned gate_sessions = 0;

static bool mode(const char *name) {
    const char *value = std::getenv("NF_MOCK_MODE");
    return value && !std::strcmp(value, name);
}

static bool tagged() {
    const char *value = std::getenv("NF_MOCK_MODE");
    return value && !std::strncmp(value, "tagged", 6);
}
static void log_call(const char *name, int channel = -1, int value = -1) {
    const char *path = std::getenv("NF_MOCK_LOG");
    if (!path) return;
    FILE *file = std::fopen(path, "a");
    if (file) { std::fprintf(file, "%s %d %d\n", name, channel, value); std::fclose(file); }
}
int rp_InitReset(bool reset) { log_call("init", -1, reset); return 0; }
rp_calib_error rp_CalibInit() { log_call("factory_calibration"); return static_cast<rp_calib_error>(0); }
int rp_Release() { log_call("release"); return 0; }
int rp_HPGetBaseFastADCSpeedHz(uint32_t *rate) { *rate = mode("wrong_model") ? 250000000 : 125000000; return 0; }
int rp_AcqReset() { log_call("reset"); return 0; }
int rp_AcqStop() { log_call("stop"); return 0; }
int rp_AcqStart() { fill_reads = 0; log_call("start"); return 0; }
int rp_DpinSetDirection(rp_dpin_t pin, rp_pinDirection_t direction) {
    gate_reads = 0; log_call("gate_direction", pin, direction);
    gate_sessions++;
    return mode("gate_direction_failure") ? 17 : 0;
}
int rp_DpinGetState(rp_dpin_t pin, rp_pinState_t *state) {
    gate_reads++;
    bool low = mode("gate_low") || (mode("gate_second_low") && gate_sessions == 2) ||
        (mode("gate_rises") && gate_reads < 3) ||
        (mode("gate_falls") && gate_reads >= 3) ||
        (mode("gate_falls_at_fill") && gate_reads >= 3);
    *state = low ? RP_LOW : RP_HIGH;
    int logged = low ? RP_LOW : RP_HIGH;
    if (mode("gate_invalid")) {
        logged = 7;
        static_assert(sizeof(*state) == sizeof(logged));
        std::memcpy(state, &logged, sizeof(logged));
    }
    log_call("gate_read", pin, logged);
    return mode("gate_read_failure") || (mode("gate_read_failure_active") && gate_reads >= 3) ? 17 : 0;
}
int rp_AcqSetTriggerSrc(rp_acq_trig_src_t source) { log_call("trigger", -1, source); return 0; }
int rp_AcqSetTriggerLevel(rp_channel_trigger_t channel, float value) { log_call("level", channel, (int)value); return 0; }
int rp_AcqAxiGetMemoryRegion(uint32_t *start, uint32_t *bytes) {
    *start = 0x1000000; *bytes = mode("small_memory") ? 4096 : 0x2000000; return 0;
}
int rp_AcqAxiSetDecimationFactor(uint32_t factor) { log_call("decimation", -1, factor); return 0; }
int rp_AcqAxiSetBufferSamples(rp_channel_t channel, uint32_t address, uint32_t size) {
    log_call("buffer", channel, size);
    if (address % 4096 || size % 64) return 17;
    auto &buffer = dma[channel]; buffer.resize(size);
    uint32_t start = (tagged() ? 101 : 100 + channel) % size;
    for (uint32_t i = 0; i < size; ++i) {
        int value = static_cast<int>(i / 4 + 1 + channel);
        if ((i / 4) & 1) value = -value;
        // Both signs and ignored upper storage bits, crossing the circular end.
        unsigned tags = (i & 3) << 14;
        if (tagged()) {
            tags = 0x8000;
            if ((mode("tagged_one_high") || (mode("tagged_second_high") && gate_sessions == 1)) && i == 20)
                tags = 0xc000;
            if (mode("tagged_all_high") || (mode("tagged_alternating") && (i / 625000) % 2 == 0))
                tags = 0xc000;
            if (mode("tagged_disagree") && channel == RP_CH_2 && i == 20) tags = 0xc000;
            if (mode("tagged_marker") && channel == RP_CH_1 && i == size - 1)
                tags = 0x4000;
        }
        buffer[(start + i) % size] = static_cast<int16_t>((value & 16383) | tags);
    }
    return 0;
}
int rp_AcqAxiSetTriggerDelay(rp_channel_t channel, int32_t count) { log_call("delay", channel, count); return 0; }
int rp_AcqAxiEnable(rp_channel_t channel, bool enabled) {
    log_call("enable", channel, enabled);
    return enabled && channel == RP_CH_2 && mode("enable_b_failure") ? 17 : 0;
}
int rp_AcqAxiGetBufferFillState(rp_channel_t channel, bool *filled) {
    fill_reads++;
    log_call("fill", channel);
    *filled = !(channel == RP_CH_2 && mode("timeout")) &&
              !((mode("gate_falls") || mode("gate_read_failure_active") || mode("slow_fill")) && fill_reads <= 2);
    return 0;
}
int rp_AcqAxiGetWritePointerAtTrig(rp_channel_t channel, uint32_t *position) {
    *position = tagged() && !mode("tagged_misaligned") ? 101 : 100 + channel;
    log_call("position", channel, *position); return 0;
}
int rp_AcqAxiGetDataV(rp_channel_t channel, uint32_t position, uint32_t *size, float *data) {
    log_call("read", channel, position);
    if (position != 100 + static_cast<unsigned>(channel)) return 17;
    if (channel == RP_CH_2 && mode("short_read")) (*size)--;
    const auto &buffer = dma[channel];
    if (buffer.empty()) return 17;
    for (uint32_t i = 0; i < *size; ++i) {
        uint16_t word = static_cast<uint16_t>(buffer[(position + i) % buffer.size()]);
        int value = static_cast<int>(word & 8191) - static_cast<int>(word & 8192);
        if (mode("software_calibration")) value -= 3 + channel;
        data[i] = static_cast<float>(value) / 8192.0f;
    }
    return 0;
}

int rp_AcqAxiGetDataRawDirect(rp_channel_t channel, uint32_t position, uint32_t size,
                             std::vector<std::span<int16_t>> *regions) {
    log_call("raw", channel, position);
    if (position != (tagged() ? 101u : 100u + static_cast<unsigned>(channel)) || mode("raw_failure")) return 17;
    auto &buffer = dma[channel];
    if (buffer.empty() || size > buffer.size()) return 17;
    if (channel == RP_CH_2 && mode("short_read")) size--;
    regions->clear();
    size_t start = position % buffer.size(), first = buffer.size() - start;
    if (first > size) first = size;
    regions->emplace_back(buffer.data() + start, first);
    if (first < size) regions->emplace_back(buffer.data(), size - first);
    return 0;
}

int rp_AcqGet16BitMode(bool *wide) { *wide = mode("wide_adc"); return 0; }
int rp_HPGetFastADCBits(uint8_t *bits) { *bits = 14; return 0; }
int rp_HPGetFastADCIsSigned(bool *is_signed) { *is_signed = !mode("unsigned_adc"); return 0; }
int rp_AcqGetCalibInFPGA(rp_channel_t, bool *in_fpga) { *in_fpga = !mode("software_calibration"); return 0; }
int rp_AcqGetGain(rp_channel_t channel, rp_pinState_t *gain) {
    log_call("parameters", channel); *gain = RP_LOW; return 0;
}
int rp_AcqGetGainV(rp_channel_t, float *gain) { *gain = 1; return 0; }
int rp_AcqAxiGetOffset(rp_channel_t, float *offset) { *offset = 0; return 0; }
int rp_HPGetHWADCFullScale(float *scale) { *scale = 1; return 0; }
int rp_AcqGetAC_DC(rp_channel_t, rp_acq_ac_dc_mode_t *coupling) { *coupling = RP_DC; return 0; }
rp_calib_error rp_CalibGetFastADCCalibValueI(rp_channel_calib_t channel,
                                           rp_acq_ac_dc_mode_calib_t,
                                           uint_gain_calib_t *calibration) {
    *calibration = {};
    calibration->gain = calibration->base = 1;
    calibration->offset = 3 + (channel == RP_CH_2_CALIB);
    return static_cast<rp_calib_error>(0);
}
rp_calib_error rp_CalibGetFastADCCalibValue_1_20I(rp_channel_calib_t channel,
                                                rp_acq_ac_dc_mode_calib_t coupling,
                                                uint_gain_calib_t *calibration) {
    return rp_CalibGetFastADCCalibValueI(channel, coupling, calibration);
}

int rp_GenReset() { log_call("gen_reset"); return 0; }
int rp_GenOutDisable(rp_channel_t channel) { log_call("gen_disable", channel); return 0; }
int rp_GenOutEnable(rp_channel_t channel) { log_call("gen_enable", channel); return 0; }
int rp_GenMode(rp_channel_t channel, rp_gen_mode_t value) { log_call("gen_mode", channel, value); return 0; }
int rp_GenWaveform(rp_channel_t channel, rp_waveform_t value) { log_call("gen_waveform", channel, value); return 0; }
int rp_GenFreq(rp_channel_t channel, float) { log_call("gen_frequency", channel); return 0; }
int rp_GenAmp(rp_channel_t channel, float) { log_call("gen_amplitude", channel); return 0; }
int rp_GenOffset(rp_channel_t channel, float) { log_call("gen_offset", channel); return 0; }
int rp_GenPhase(rp_channel_t channel, float) { log_call("gen_phase", channel); return 0; }
int rp_GenTriggerOnly(rp_channel_t channel) { log_call("gen_trigger", channel); return 0; }
