#include "nullifier.h"
#include <stdio.h>

int nf_acquire_raw(nf_processor *p, nf_raw_channel channels[2], char *error) {
    (void)p; (void)channels;
    snprintf(error, NF_ERROR_SIZE, "raw capture requires the board SDK"); return -1;
}
int nf_acquire_raw_preview(nf_processor *p, nf_raw_channel channels[2],
                           unsigned points, float **a, float **b, char *error) {
    (void)points; *a = *b = NULL; return nf_acquire_raw(p, channels, error);
}

int nf_acquire(const nf_config *config, float **a, float **b, char *error) {
    (void)config; (void)a; (void)b;
    snprintf(error, NF_ERROR_SIZE, "this build has no Red Pitaya SDK; use --replay or build the board target");
    return -1;
}

int nf_acquire_modes(nf_processor *p, unsigned preview_points, float **a, float **b,
                     double *q[2], size_t *modes, char *error) {
    (void)preview_points; q[0] = q[1] = NULL; *modes = 0;
    return nf_acquire(&p->config, a, b, error);
}

int nf_acquire_shutdown(char *error) { (void)error; return 0; }
nf_acquire_timing nf_acquire_last_timing(void) {
    nf_acquire_timing empty = {0}; return empty;
}
int nf_read_voltage(unsigned channel, uint32_t position, unsigned count,
                    float *output, bool *direct, char *error) {
    (void)channel; (void)position; (void)count; (void)output; (void)direct;
    snprintf(error, NF_ERROR_SIZE, "direct ADC readout requires the board SDK"); return -1;
}

int nf_set_output(unsigned channel, bool enabled, unsigned waveform, double frequency,
                  double amplitude, double offset, double phase, char *error) {
    (void)channel; (void)enabled; (void)waveform; (void)frequency;
    (void)amplitude; (void)offset; (void)phase;
    snprintf(error, NF_ERROR_SIZE, "output generation requires the board SDK build");
    return -1;
}
