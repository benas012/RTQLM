/* Test double compiled against the same official headers as the real adapter. */
#include <rp.h>
#include <rp_hw-profiles.h>
#include <rp_hw_calib.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>

static bool mode(const char *name) {
    const char *value = std::getenv("NF_MOCK_MODE");
    return value && !std::strcmp(value, name);
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
int rp_AcqStart() { log_call("start"); return 0; }
int rp_AcqSetTriggerSrc(rp_acq_trig_src_t source) { log_call("trigger", -1, source); return 0; }
int rp_AcqSetTriggerLevel(rp_channel_trigger_t channel, float value) { log_call("level", channel, (int)value); return 0; }
int rp_AcqAxiGetMemoryRegion(uint32_t *start, uint32_t *bytes) {
    *start = 0x1000000; *bytes = mode("small_memory") ? 4096 : 0x2000000; return 0;
}
int rp_AcqAxiSetDecimationFactor(uint32_t factor) { log_call("decimation", -1, factor); return 0; }
int rp_AcqAxiSetBufferSamples(rp_channel_t channel, uint32_t address, uint32_t size) {
    log_call("buffer", channel, size); return address % 4096 || size % 64 ? 17 : 0;
}
int rp_AcqAxiSetTriggerDelay(rp_channel_t channel, int32_t count) { log_call("delay", channel, count); return 0; }
int rp_AcqAxiEnable(rp_channel_t channel, bool enabled) {
    log_call("enable", channel, enabled);
    return enabled && channel == RP_CH_2 && mode("enable_b_failure") ? 17 : 0;
}
int rp_AcqAxiGetBufferFillState(rp_channel_t channel, bool *filled) {
    log_call("fill", channel); *filled = !(channel == RP_CH_2 && mode("timeout")); return 0;
}
int rp_AcqAxiGetWritePointerAtTrig(rp_channel_t channel, uint32_t *position) {
    *position = 100 + channel; log_call("position", channel, *position); return 0;
}
int rp_AcqAxiGetDataV(rp_channel_t channel, uint32_t position, uint32_t *size, float *data) {
    log_call("read", channel, position);
    if (position != 100 + static_cast<unsigned>(channel)) return 17;
    if (channel == RP_CH_2 && mode("short_read")) (*size)--;
    for (uint32_t i = 0; i < *size; ++i) data[i] = static_cast<float>((i / 4) + 1 + channel);
    return 0;
}
