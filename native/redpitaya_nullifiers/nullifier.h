#ifndef RTQLM_NULLIFIER_H
#define RTQLM_NULLIFIER_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define NF_MAX_ROWS 16
#define NF_MAX_TERMS 32
#define NF_MAX_TEMPLATE 4096
#define NF_MAX_HISTORY 262144
#define NF_DEFAULT_HISTORY 25000
#define NF_MAX_CAPTURE_SAMPLES 8388608
#define NF_ERROR_SIZE 256
#define NF_TAGGED_LONG_BIN UINT32_C(0x80000000)
#define NF_TAGGED_OFFSET_MASK UINT32_C(0x7fffffff)
#define NF_TAGGED_GAP UINT32_MAX

typedef struct { unsigned source, offset; double weight; } nf_term;
typedef struct {
    char name[64];
    unsigned start, term_count;
    nf_term terms[NF_MAX_TERMS];
} nf_row;

typedef struct {
    unsigned decimation, capture_samples, measurement_samples, history_modes, mode_samples, min_samples;
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
    unsigned sampled_counts[2];
    double sampled_templates[2][NF_MAX_TEMPLATE];
    nf_row rows[NF_MAX_ROWS];
    char acquisition_gate_pin[16], raw_readout[16];
    unsigned acquisition_gate_poll_us;
} nf_config;

typedef struct { uint64_t count; double mean, m2; } nf_moments;
/* Non-overlapping CPU stages; projection and acquisition are timed separately. */
typedef struct {
    double normalization_ms, formulas_ms, rolling_ms, statistics_ms, total_ms;
    uint64_t log10_calls;
} nf_process_timing;
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
    bool enabled, calibrated, calibrating, has_result, truncated, raw_counts;
    bool tagged_capture;
    size_t captured_modes, included_modes, excluded_modes;
    double sample_rate, mode_step, mode[NF_MAX_TEMPLATE], mode_large[NF_MAX_TEMPLATE], coefficients[2];
    unsigned mode_size, mode_size_max;
    uint32_t mode_remainder, mode_denominator;
    unsigned raw_boundary_count;
    uint32_t raw_boundaries[NF_MAX_HISTORY + 1];
    uint32_t *tagged_bins;
    size_t tagged_capacity;
    double *quadratures[2];
    size_t quadrature_count;
    nf_moments calibration[2], segment_variances[2];
    nf_row_state rows[NF_MAX_ROWS];
    nf_process_timing process_timing;
    /* Owned spare buffers, detached before returning projection output to callers. */
    double *spare_quadratures[2];
    size_t spare_capacity;
} nf_processor;

/* Stopped 14-bit ADC DMA view: circular wrap gives at most two regions. */
typedef struct {
    const int16_t *data[2];
    size_t size[2];
    double zero;
    const float *volts; /* Optional exact SDK lookup for a voltage rejection gate. */
} nf_raw_channel;

typedef struct {
    size_t start[2], modes;
    bool truncated;
    /* Per-slot sample offsets relative to start[], with a size bit and gap
     * sentinel. NULL selects the uninterrupted calibration clock. */
    const uint32_t *bins;
} nf_tagged_plan;

#ifdef __cplusplus
extern "C" {
#endif
void nf_defaults(nf_config *config);
int nf_configure(nf_processor *processor, const nf_config *config, char *error);
void nf_clear_results(nf_processor *processor);
void nf_clear_calibration(nf_processor *processor);
void nf_start_calibration(nf_processor *processor);
int nf_finish_calibration(nf_processor *processor, uint64_t minimum, char *error);
int nf_process(nf_processor *processor, const float *a, const float *b, size_t count, char *error);
int nf_process_at(nf_processor *processor, const float *a, const float *b, size_t count,
                  uint64_t first_mode, char *error);
int nf_project_raw(nf_processor *processor, const nf_raw_channel channels[2], size_t count,
                   double *quadratures[2], size_t *modes, char *error);
/* Same projection/ownership contract; adapter entry optimized for stopped DMA. */
int nf_project_raw_dma(nf_processor *processor, const nf_raw_channel channels[2], size_t count,
                       double *quadratures[2], size_t *modes, char *error);
/* Tagged readouts are explicit so stock boards keep using the calibrated FPGA path. */
bool nf_tagged_readout(const char *name, size_t *tile_words, bool *parallel);
/* Validate both raw rails with nf_validate_tagged_pair before preparing a plan. */
int nf_prepare_tagged(nf_processor *p, const nf_raw_channel raw[2], size_t measurement,
                      nf_tagged_plan *plan, double *q[2], char *error);
int nf_validate_tagged_pair(const nf_raw_channel raw[2], char *error);
/* raw covers the full capture. A tile, if supplied, holds at least one complete mode. */
uint16_t nf_project_tagged_rail(const nf_processor *p, const nf_raw_channel *raw,
                               const nf_tagged_plan *plan, unsigned channel, int16_t *tile,
                               size_t tile_words, double *q);
void nf_finish_tagged(nf_processor *p, const nf_tagged_plan *plan, double *q[2], bool accepted);
/* Consumes both allocated quadrature arrays on success or failure. */
int nf_process_modes(nf_processor *processor, double *quadratures[2], size_t modes, char *error);
nf_process_timing nf_process_last_timing(const nf_processor *processor);
uint64_t nf_mode_boundary(const nf_processor *processor, uint64_t index);
size_t nf_mode_count(const nf_processor *processor, size_t samples, uint64_t first_mode);
void nf_destroy(nf_processor *processor);

/* The vendor SDK adapter exposes a C ABI; all calculation and command code is C. */
int nf_acquire(const nf_config *config, float **a, float **b, char *error);
int nf_acquire_raw(nf_processor *processor, nf_raw_channel channels[2], char *error);
int nf_acquire_raw_preview(nf_processor *processor, nf_raw_channel channels[2],
                           unsigned points, float **a, float **b, char *error);
int nf_acquire_modes(nf_processor *processor, unsigned preview_points, float **a, float **b,
                     double *quadratures[2], size_t *modes, char *error);
typedef struct {
    double setup_ms, wait_ms, read_ms, channel_read_ms[2];
    double read_parameters_ms, read_table_ms, read_regions_ms, read_convert_ms;
    double read_verify_ms, read_fallback_ms, read_pointers_ms, raw_projection_ms;
    unsigned direct_channels;
    unsigned preview_stride;
    uint32_t trigger_positions[2];
    double cleanup_ms, total_ms;
    double gate_wait_ms, gate_check_ms, gate_max_interval_ms;
    double raw_copy_ms, sample_duration_ms, start_interval_ms;
    uint64_t gate_checks, raw_copy_bytes;
    double gate_interval_sum_ms;
    uint64_t gate_intervals;
    /* 0.1 ms buckets [0,.1), ... [3.0,3.1), [3.1,infinity). */
    uint64_t gate_interval_histogram[32];
} nf_acquire_timing;
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
