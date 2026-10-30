#ifndef RTQLM_NULLIFIER_H
#define RTQLM_NULLIFIER_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define NF_MAX_ROWS 16
#define NF_MAX_TERMS 32
#define NF_MAX_TEMPLATE 4096
#define NF_MAX_HISTORY 25000
#define NF_ERROR_SIZE 256

typedef struct { unsigned source, offset; double weight; } nf_term;
typedef struct {
    char name[64];
    unsigned start, term_count;
    nf_term terms[NF_MAX_TERMS];
} nf_row;

typedef struct {
    unsigned decimation, capture_samples, history_modes, mode_samples, min_samples;
    unsigned oversample, variance_modes, end_guard;
    int b_delay;
    double mode_width, frequency, variance_seconds, guard_seconds, timeout_seconds;
    double trigger_level, reject_threshold;
    double lock_seconds, collect_seconds;
    char window_mode[16];
    bool require_full_variance;
    char shape[40], estimator[16], calibration_estimator[16], trigger[16], basis[2];
    unsigned template_count, row_count;
    double explicit_template[NF_MAX_TEMPLATE];
    nf_row rows[NF_MAX_ROWS];
} nf_config;

typedef struct { uint64_t count; double mean, m2; } nf_moments;
typedef struct {
    size_t size;
    double *values, *variance, *db;
    nf_moments *ensemble;
    double *variance_sum, *db_sum;
    uint64_t *counts;
} nf_row_state;

typedef struct {
    nf_config config;
    uint64_t revision, captures;
    bool enabled, calibrated, calibrating, has_result, truncated;
    double sample_rate, mode[NF_MAX_TEMPLATE], coefficients[2];
    unsigned mode_size;
    double *quadratures[2];
    size_t quadrature_count;
    nf_moments calibration[2], segment_variances[2];
    nf_row_state rows[NF_MAX_ROWS];
} nf_processor;

void nf_defaults(nf_config *config);
int nf_configure(nf_processor *processor, const nf_config *config, char *error);
void nf_clear_results(nf_processor *processor);
void nf_clear_calibration(nf_processor *processor);
void nf_start_calibration(nf_processor *processor);
int nf_finish_calibration(nf_processor *processor, uint64_t minimum, char *error);
int nf_process(nf_processor *processor, const float *a, const float *b, size_t count, char *error);
void nf_destroy(nf_processor *processor);

/* The vendor SDK adapter exposes a C ABI; all calculation and command code is C. */
#ifdef __cplusplus
extern "C" {
#endif
int nf_acquire(const nf_config *config, float **a, float **b, char *error);
typedef struct { double setup_ms, wait_ms, read_ms; unsigned direct_channels; } nf_acquire_timing;
nf_acquire_timing nf_acquire_last_timing(void);
int nf_read_voltage(unsigned channel, uint32_t position, unsigned count,
                    float *output, bool *direct, char *error);
int nf_acquire_shutdown(char *error);
int nf_set_output(unsigned channel, bool enabled, unsigned waveform, double frequency,
                  double amplitude, double offset, double phase, char *error);
#ifdef __cplusplus
}
#endif
#endif
