#define _POSIX_C_SOURCE 200809L
#include "nullifier.h"

#include <math.h>
#include <float.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#if defined(__ARM_NEON)
#include <arm_neon.h>
#endif

#define PI 3.14159265358979323846
#define FAIL(message) do { snprintf(error, NF_ERROR_SIZE, "%s", message); return -1; } while (0)

static void observe(nf_moments *moments, double value) {
    if (!isfinite(value)) return;
    double delta = value - moments->mean;
    moments->count++;
    moments->mean += delta / (double)moments->count;
    moments->m2 += delta * (value - moments->mean);
}

static double variance(const nf_moments *moments) {
    return moments->count >= 2 ? fmax(moments->m2 / (double)moments->count, 0) : NAN;
}

static void remove_value(nf_moments *moments, double value) {
    if (!isfinite(value)) return;
    if (moments->count <= 1) { memset(moments, 0, sizeof(*moments)); return; }
    double old_mean = moments->mean;
    moments->count--;
    moments->mean -= (value - old_mean) / (double)moments->count;
    moments->m2 = fmax(0, moments->m2 - (value - old_mean) * (value - moments->mean));
}

void nf_defaults(nf_config *c) {
    memset(c, 0, sizeof(*c));
    c->decimation = 1; c->capture_samples = 625024; c->history_modes = NF_DEFAULT_HISTORY;
    c->min_samples = 4; c->oversample = 1000; c->b_delay = 2;
    c->mode_width = 308e-9; c->frequency = 6e6; c->variance_seconds = 1.0 / 3000;
    c->guard_seconds = 0.0008006; c->end_guard = 1000; c->timeout_seconds = 5;
    c->lock_seconds = 0.005; c->collect_seconds = 0.005; strcpy(c->window_mode, "periodic");
    strcpy(c->acquisition_gate_pin, "DIO0_P"); c->acquisition_gate_poll_us = 500;
    strcpy(c->raw_readout, "direct_dma");
    strcpy(c->shape, "lab_gaussian_linear_cosine"); strcpy(c->estimator, "lab_rolling");
    strcpy(c->calibration_estimator, "segment_mean"); strcpy(c->trigger, "now");
    strcpy(c->basis, "x"); c->row_count = 1;
    nf_row *row = &c->rows[0]; strcpy(row->name, "cluster_x"); row->term_count = 4;
    row->terms[0] = (nf_term){0, 0, 1}; row->terms[1] = (nf_term){0, 1, 1};
    row->terms[2] = (nf_term){1, 0, 1}; row->terms[3] = (nf_term){1, 1, -1};
}

static double analytic(double time, double frequency, bool lab) {
    double angular = 2 * PI * frequency;
    return time * exp(-0.5 * angular * angular * time * time) *
           (lab ? cos(time * frequency) : 1);
}

static int make_fixed_mode(nf_processor *p, unsigned n, double width, char *error) {
    const nf_config *c = &p->config;
    double dt = 1 / p->sample_rate;
    bool lab = strcmp(c->shape, "lab_gaussian_linear_cosine") == 0;
    if (!n) {
        double points = width / dt;
        if (points > NF_MAX_TEMPLATE) FAIL("mode exceeds 4096 samples");
        n = (unsigned)(lab ? floor(nextafter(points, INFINITY)) : nearbyint(points));
    }
    if (n < c->min_samples || n > NF_MAX_TEMPLATE) FAIL("invalid samples per temporal mode");
    p->mode_size = n;
    if (c->sampled_counts[0]) {
        unsigned index = n == c->sampled_counts[0] ? 0 : 1;
        if (c->sampled_counts[index] != n) FAIL("sampled template length does not match temporal bin");
        double energy = 0;
        for (unsigned i = 0; i < n; ++i) {
            double value = c->sampled_templates[index][i];
            if (!isfinite(value)) FAIL("sampled template must be finite");
            energy += value * value * dt;
        }
        if (!(energy > 0) || !isfinite(energy)) FAIL("invalid sampled template norm");
        memcpy(p->mode, c->sampled_templates[index], n * sizeof(double));
        return 0; /* Host already applied the selected sampling/normalization convention. */
    }
    if (c->template_count) memcpy(p->mode, c->explicit_template, n * sizeof(double));
    else if (strcmp(c->shape, "identity") == 0) {
        for (unsigned i = 0; i < n; ++i) p->mode[i] = 1;
    } else if (lab) {
        double fine_dt = dt / c->oversample;
        size_t fine_n = (size_t)ceil(width / fine_dt);
        if (fine_n < 2 || fine_n > 2000000) FAIL("lab coefficient grid exceeds its limit");
        double *fine = calloc(fine_n, sizeof(double));
        if (!fine) FAIL("cannot allocate coefficient grid");
        double norm = 0;
        for (size_t i = 0; i < fine_n; ++i) {
            fine[i] = analytic(((double)i - (double)(fine_n / 2)) * fine_dt, c->frequency, true);
            norm += fine[i] * fine[i] * fine_dt;
        }
        norm = sqrt(norm);
        if (!(norm > 0) || !isfinite(norm)) { free(fine); FAIL("invalid mode norm"); }
        for (unsigned i = 0; i < n; ++i) {
            double position = i * width / n / fine_dt;
            size_t left = (size_t)floor(position);
            if (left >= fine_n - 1) p->mode[i] = fine[fine_n - 1] / norm;
            else p->mode[i] = (fine[left] + (position - left) * (fine[left + 1] - fine[left])) / norm;
        }
        free(fine);
        return 0; /* Lab normalization intentionally precedes interpolation. */
    } else {
        if ((uint64_t)n * c->oversample > 2000000) FAIL("coefficient grid exceeds its limit");
        for (unsigned i = 0; i < n; ++i) {
            p->mode[i] = 0;
            for (unsigned j = 0; j < c->oversample; ++j) {
                double time = (i + (j + 0.5) / c->oversample) * dt - n * dt / 2;
                if (fabs(time) < width / 2) p->mode[i] += analytic(time, c->frequency, false);
            }
            p->mode[i] /= c->oversample;
        }
    }
    double norm = 0;
    for (unsigned i = 0; i < n; ++i) norm += p->mode[i] * p->mode[i] * dt;
    norm = sqrt(norm);
    if (!(norm > 0) || !isfinite(norm)) FAIL("invalid mode norm");
    for (unsigned i = 0; i < n; ++i) p->mode[i] /= norm;
    return 0;
}

static void unit_noise_gain(double *mode, unsigned size, double rate) {
    double energy = 0;
    for (unsigned i = 0; i < size; ++i) energy += mode[i] * mode[i] / rate;
    double norm = sqrt(energy);
    for (unsigned i = 0; i < size; ++i) mode[i] /= norm;
}

static unsigned fixed_mode_size(const nf_config *c) {
    return c->template_count ? c->template_count : c->mode_samples;
}

static bool same_mode_clock(const nf_processor *p, const nf_processor *candidate) {
    const nf_config *c = &candidate->config;
    unsigned fixed = fixed_mode_size(c);
    return p->enabled && p->sample_rate == candidate->sample_rate &&
           fixed == fixed_mode_size(&p->config) &&
           (fixed || p->config.mode_width == c->mode_width);
}

static int resolve_mode_schedule(nf_processor *p, char *error) {
    unsigned fixed = fixed_mode_size(&p->config);
    double step = fixed ? fixed : p->config.mode_width * p->sample_rate;
    if (!isfinite(step)) FAIL("invalid samples per temporal mode");
    double tolerance = 16 * DBL_EPSILON * fmax(step, 1);
    double nearest = nearbyint(step);
    if (fabs(step - nearest) <= tolerance) step = nearest;
    if (step > NF_MAX_TEMPLATE || floor(step) < p->config.min_samples)
        FAIL("invalid samples per temporal mode");
    unsigned shortest = (unsigned)step;
    double fraction = step - shortest;
    uint32_t numerator = 0, denominator = 1;
    if (fraction) {
        /* Keep in sync with resolve_mode_schedule in post_processing.py. */
        const uint32_t limit = 1000000000;
        uint32_t previous_num = 1, previous_den = 0;
        double value = fraction;
        while (value) {
            double reciprocal = 1 / value;
            if (reciprocal >= limit + 1) break;
            uint32_t term = (uint32_t)reciprocal;
            uint64_t next_den = (uint64_t)term * denominator + previous_den;
            if (next_den > limit) break;
            uint32_t next_num = term * numerator + previous_num;
            previous_num = numerator; numerator = next_num;
            previous_den = denominator; denominator = (uint32_t)next_den;
            if (fabs((double)numerator / denominator - fraction) <= tolerance) break;
            value = reciprocal - term;
        }
        if (fabs((double)numerator / denominator - fraction) > tolerance) {
            double rounded = nearbyint(fraction * limit);
            numerator = (uint32_t)fmin(fmax(rounded, 1), limit - 1);
            denominator = limit;
        }
    }
    p->mode_size = shortest;
    p->mode_size_max = shortest + (numerator != 0);
    p->mode_remainder = numerator; p->mode_denominator = denominator;
    p->mode_step = shortest + (double)numerator / denominator;
    return 0;
}

static int make_mode(nf_processor *p, unsigned small, char *error) {
    /* Configuration-time quadrature weights: never regenerate exp/cos or the
     * oversampled integration grid per capture. Fractional boundaries retain
     * the requested period; each supported bin length keeps unit noise gain.
     * gaussian_linear is Larsen Eq. (3.25); the separate lab shape deliberately
     * retains its established cosine/interpolation convention. */
    const nf_config *c = &p->config;
    unsigned large = p->mode_size_max;
    if (small < c->min_samples) FAIL("invalid samples per temporal mode");
    if (c->sampled_counts[0] &&
        (c->template_count || c->sampled_counts[0] != small ||
         c->sampled_counts[1] != (large == small ? 0 : large)))
        FAIL("sampled templates must match the shortest and longest temporal bins");
    if (small == large)
        return make_fixed_mode(p, small, c->mode_samples ? small / p->sample_rate : c->mode_width, error);
    if (make_fixed_mode(p, large, nextafter(large / p->sample_rate, INFINITY), error)) return -1;
    unit_noise_gain(p->mode, large, p->sample_rate);
    memcpy(p->mode_large, p->mode, large * sizeof(double));
    if (make_fixed_mode(p, small, nextafter(small / p->sample_rate, INFINITY), error)) return -1;
    unit_noise_gain(p->mode, small, p->sample_rate);
    return 0;
}

uint64_t nf_mode_boundary(const nf_processor *p, uint64_t index) {
    if (!p->mode_remainder) return index * p->mode_size;
    uint64_t rounds = index / p->mode_denominator;
    uint64_t phase = (index % p->mode_denominator) * p->mode_remainder;
    return index * p->mode_size + rounds * p->mode_remainder + phase / p->mode_denominator;
}

static unsigned next_mode_size(const nf_processor *p, uint32_t *phase) {
    *phase += p->mode_remainder;
    if (*phase < p->mode_denominator) return p->mode_size;
    *phase -= p->mode_denominator;
    return p->mode_size + 1;
}

size_t nf_mode_count(const nf_processor *p, size_t samples, uint64_t first_mode) {
    if (!p->mode_remainder) return samples / p->mode_size;
    uint32_t denominator = p->mode_denominator;
    if (samples < UINT64_MAX / denominator) {
        uint64_t phase = ((first_mode % denominator) * p->mode_remainder) % denominator;
        uint64_t step = (uint64_t)p->mode_size * denominator + p->mode_remainder;
        return (((uint64_t)samples + 1) * denominator - 1 - phase) / step;
    }
    uint64_t origin = nf_mode_boundary(p, first_mode);
    size_t modes = (size_t)(samples / p->mode_step);
    while (modes && nf_mode_boundary(p, first_mode + modes) - origin > samples) modes--;
    while (nf_mode_boundary(p, first_mode + modes + 1) - origin <= samples) modes++;
    return modes;
}

void nf_clear_results(nf_processor *p) {
    free(p->quadratures[0]); free(p->quadratures[1]);
    free(p->spare_quadratures[0]); free(p->spare_quadratures[1]);
    p->spare_quadratures[0] = p->spare_quadratures[1] = NULL; p->spare_capacity = 0;
    p->quadratures[0] = p->quadratures[1] = NULL; p->quadrature_count = 0;
    for (unsigned i = 0; i < NF_MAX_ROWS; ++i) {
        nf_row_state *r = &p->rows[i];
        free(r->values); free(r->variance); free(r->db); free(r->ensemble);
        free(r->variance_sum); free(r->db_sum); free(r->counts);
        memset(r, 0, sizeof(*r));
    }
    p->has_result = false; p->captures = 0; p->truncated = false;
    p->tagged_capture = false;
    p->captured_modes = p->included_modes = p->excluded_modes = 0;
}

void nf_clear_calibration(nf_processor *p) {
    nf_clear_results(p);
    memset(p->calibration, 0, sizeof(p->calibration));
    memset(p->segment_variances, 0, sizeof(p->segment_variances));
    p->calibrated = false; p->calibrating = false;
    p->coefficients[0] = p->coefficients[1] = 1;
}

static double reference(const nf_row *row) {
    double total = 0;
    for (unsigned i = 0; i < row->term_count; ++i) {
        const nf_term *term = &row->terms[i];
        double combined = term->weight;
        bool seen = false;
        for (unsigned j = 0; j < i; ++j)
            if (row->terms[j].source == term->source && row->terms[j].offset == term->offset) seen = true;
        if (seen) continue;
        for (unsigned j = i + 1; j < row->term_count; ++j)
            if (row->terms[j].source == term->source && row->terms[j].offset == term->offset)
                combined += row->terms[j].weight;
        total += 0.5 * combined * combined;
    }
    return total;
}

bool nf_tagged_readout(const char *name, size_t *tile_words, bool *parallel) {
    static const struct { const char *name; size_t words; bool parallel; } options[] = {
        {"e1_direct", 0, false}, {"e1_bulk", NF_MAX_CAPTURE_SAMPLES, false},
        {"e1_tile16", 8192, false}, {"e1_tile64", 32768, false},
        {"e1_tile256", 131072, false}, {"e1_tile64x2", 32768, true},
    };
    for (size_t i = 0; i < sizeof(options) / sizeof(options[0]); ++i) {
        if (strcmp(name, options[i].name)) continue;
        if (tile_words) *tile_words = options[i].words;
        if (parallel) *parallel = options[i].parallel;
        return true;
    }
    return false;
}

int nf_configure(nf_processor *p, const nf_config *c, char *error) {
    if ((strcmp(c->acquisition_gate_pin, "DIO0_P") &&
         strcmp(c->acquisition_gate_pin, "DIO3_P") && strcmp(c->acquisition_gate_pin, "DIO3_N") &&
         c->acquisition_gate_pin[0]) || c->acquisition_gate_poll_us < 50 ||
        c->acquisition_gate_poll_us > 1000000) FAIL("invalid acquisition gate pin or poll interval (50..1000000 us)");
    bool tagged = nf_tagged_readout(c->raw_readout, NULL, NULL);
    if (!tagged && strcmp(c->raw_readout, "direct_dma") && strcmp(c->raw_readout, "ram_copy"))
        FAIL("unsupported rawReadout");
    if (tagged && (c->decimation != 1 || strcmp(c->trigger, "now") ||
                   strcmp(c->acquisition_gate_pin, "DIO0_P") || c->reject_threshold != 0))
        FAIL("tagged E1 requires decimation 1, immediate trigger, DIO0_P and no voltage threshold");
    if (!c->decimation || c->decimation > 65536 ||
        (c->decimation < 16 && c->decimation != 1 && c->decimation != 2 &&
         c->decimation != 4 && c->decimation != 8)) FAIL("unsupported ADC decimation");
    if (!c->capture_samples || c->capture_samples > NF_MAX_CAPTURE_SAMPLES || c->capture_samples % 64)
        FAIL("captureSamples must be a multiple of 64 between 64 and 8388608");
    if (c->measurement_samples > c->capture_samples)
        FAIL("measurementSamples must not exceed captureSamples");
    if (c->history_modes < 100 || c->history_modes > NF_MAX_HISTORY ||
        c->min_samples < 2 || c->min_samples > NF_MAX_TEMPLATE || !c->oversample ||
        c->oversample > 1000 || c->mode_samples > NF_MAX_TEMPLATE ||
        c->variance_modes > c->history_modes || (c->variance_modes && c->variance_modes < 2))
        FAIL("processing count exceeds its supported range");
    if (!(c->mode_width > 0) || !(c->frequency > 0) || !(c->variance_seconds > 0) ||
        c->guard_seconds < 0 || !(c->timeout_seconds > 0) || c->timeout_seconds > 60 ||
        c->reject_threshold < 0 || abs(c->b_delay) > 65536) FAIL("invalid timing or amplitude setting");
    if (strcmp(c->shape, "identity") && strcmp(c->shape, "gaussian_linear") &&
        strcmp(c->shape, "lab_gaussian_linear_cosine") &&
        !(c->sampled_counts[0] && !strcmp(c->shape, "gaussian_linear_cosine")))
        FAIL("unsupported modeShape");
    if (strcmp(c->estimator, "rolling") && strcmp(c->estimator, "ensemble") &&
        strcmp(c->estimator, "lab_rolling")) FAIL("unsupported varianceEstimator");
    if (strcmp(c->calibration_estimator, "pooled") &&
        strcmp(c->calibration_estimator, "segment_mean")) FAIL("unsupported calibrationEstimator");
    if (strcmp(c->trigger, "now") && strcmp(c->trigger, "external_rising") &&
        strcmp(c->trigger, "external_falling") && strcmp(c->trigger, "a_rising") &&
        strcmp(c->trigger, "a_falling") && strcmp(c->trigger, "b_rising") &&
        strcmp(c->trigger, "b_falling")) FAIL("unsupported triggerSource");
    if (strcmp(c->basis, "x") && strcmp(c->basis, "p")) FAIL("measurementBasis must be x or p");
    if (strcmp(c->window_mode, "periodic") && strcmp(c->window_mode, "continuous"))
        FAIL("native streaming currently supports periodic or continuous windows");
    if (!isfinite(c->lock_seconds) || !isfinite(c->collect_seconds) ||
        c->lock_seconds < 0 || c->collect_seconds <= 0 || c->collect_seconds > 60)
        FAIL("invalid streaming window timing");
    if (!strcmp(c->window_mode, "continuous") && strcmp(c->estimator, "rolling"))
        FAIL("continuous streaming requires rolling variance");
    if (!c->row_count || c->row_count > NF_MAX_ROWS) FAIL("invalid formula count");
    for (unsigned i = 0; i < c->row_count; ++i) {
        const nf_row *r = &c->rows[i];
        if (!r->name[0] || !r->term_count || r->term_count > NF_MAX_TERMS ||
            r->start >= c->history_modes) FAIL("invalid nullifier row");
        for (unsigned j = 0; j < i; ++j)
            if (strcmp(r->name, c->rows[j].name) == 0) FAIL("duplicate nullifier name");
        for (unsigned j = 0; j < r->term_count; ++j)
            if (r->terms[j].source > 1 || r->terms[j].offset >= c->history_modes ||
                !isfinite(r->terms[j].weight)) FAIL("invalid nullifier term");
        if (!(reference(r) > 0) || !isfinite(reference(r))) FAIL("formula weights cancel or overflow");
    }
    nf_processor *candidate = calloc(1, sizeof(*candidate));
    if (!candidate) FAIL("cannot allocate configuration");
    candidate->config = *c; candidate->sample_rate = 125000000.0 / c->decimation;
    bool reuse_clock = same_mode_clock(p, candidate);
    int result = 0;
    if (reuse_clock) {
        candidate->mode_size = p->mode_size; candidate->mode_size_max = p->mode_size_max;
        candidate->mode_step = p->mode_step;
        candidate->mode_remainder = p->mode_remainder;
        candidate->mode_denominator = p->mode_denominator;
    } else result = resolve_mode_schedule(candidate, error);
    if (!result) result = make_mode(candidate, candidate->mode_size, error);
    double guard = nearbyint(c->guard_seconds * candidate->sample_rate);
    unsigned measurement = c->measurement_samples ? c->measurement_samples : c->capture_samples;
    if (!result && (guard + c->end_guard + abs(c->b_delay) + candidate->mode_size > measurement)) {
        snprintf(error, NF_ERROR_SIZE, "capture guards and delay leave no complete temporal mode");
        result = -1;
    }
    if (!result && !strcmp(c->window_mode, "periodic")) {
        double samples = nearbyint(c->collect_seconds * candidate->sample_rate);
        if (samples > NF_MAX_CAPTURE_SAMPLES || guard + c->end_guard + abs(c->b_delay) + candidate->mode_size > samples) {
            snprintf(error, NF_ERROR_SIZE, "streaming collection guards leave no complete mode or exceed memory limit");
            result = -1;
        }
    }
    if (!result) {
        nf_clear_calibration(p); p->config = *c; p->sample_rate = candidate->sample_rate;
        p->mode_size = candidate->mode_size; p->mode_size_max = candidate->mode_size_max;
        p->mode_step = candidate->mode_step;
        p->mode_remainder = candidate->mode_remainder;
        p->mode_denominator = candidate->mode_denominator;
        if (!reuse_clock) p->raw_boundary_count = 0;
        unsigned first = p->raw_boundary_count;
        uint32_t phase = ((uint64_t)first * p->mode_remainder) % p->mode_denominator;
        for (unsigned k = first; k < c->history_modes; ++k)
            p->raw_boundaries[k + 1] = p->raw_boundaries[k] + next_mode_size(p, &phase);
        if (p->raw_boundary_count < c->history_modes) p->raw_boundary_count = c->history_modes;
        memcpy(p->mode, candidate->mode, sizeof(p->mode));
        memcpy(p->mode_large, candidate->mode_large, sizeof(p->mode_large));
        p->revision++; p->enabled = true;
    }
    free(candidate);
    return result;
}

void nf_start_calibration(nf_processor *p) { nf_clear_calibration(p); p->calibrating = true; }

int nf_finish_calibration(nf_processor *p, uint64_t minimum, char *error) {
    if (!p->enabled || !p->calibrating) FAIL("start calibration before finishing it");
    bool required[2] = {false, false};
    for (unsigned i = 0; i < p->config.row_count; ++i)
        for (unsigned j = 0; j < p->config.rows[i].term_count; ++j)
            required[p->config.rows[i].terms[j].source] = true;
    double coefficients[2] = {1, 1};
    for (unsigned channel = 0; channel < 2; ++channel) {
        if (!required[channel]) continue;
        if (p->calibration[channel].count < minimum) FAIL("not enough valid shot-noise modes");
        double v = strcmp(p->config.calibration_estimator, "segment_mean") == 0 ?
                   p->segment_variances[channel].mean : variance(&p->calibration[channel]);
        if (!(v > 0) || !isfinite(v)) FAIL("shot-noise variance is zero or invalid");
        coefficients[channel] = 1 / sqrt(2 * v);
    }
    nf_clear_results(p); memcpy(p->coefficients, coefficients, sizeof(coefficients));
    p->calibrating = false; p->calibrated = true;
    return 0;
}

static double process_clock_ms(void) {
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return (double)now.tv_sec * 1000 + (double)now.tv_nsec / 1e6;
}

nf_process_timing nf_process_last_timing(const nf_processor *p) { return p->process_timing; }

static int allocate_row(nf_row_state *r, size_t capacity, bool lab, bool ensemble) {
    if (r->values) return 0;
    r->values = calloc(capacity, sizeof(double)); r->variance = calloc(capacity, sizeof(double));
    r->db = calloc(capacity, sizeof(double));
    if (ensemble) r->ensemble = calloc(capacity, sizeof(nf_moments));
    if (lab) {
        r->variance_sum = calloc(capacity, sizeof(double)); r->db_sum = calloc(capacity, sizeof(double));
    }
    r->counts = calloc(capacity, sizeof(uint64_t));
    return r->values && r->variance && r->db && r->counts && (!ensemble || r->ensemble) &&
           (!lab || (r->variance_sum && r->db_sum)) ? 0 : -1;
}

static void rolling(const double *values, double *output, size_t count, unsigned window, bool lab, bool full) {
    size_t left = window / 2, right = lab ? left : window - left;
    size_t begin = 0, end = 0;
    nf_moments m = {0};
    double inverse_window = 1.0 / window;
    size_t support = left + right;
    double inverse_support = support == window ? inverse_window : 1.0 / support;
    for (size_t i = 0; i < count; ++i) {
        size_t next_begin = i > left ? i - left : 0;
        size_t next_end = i + right < count ? i + right : count;
        if (end + 1 == next_end && begin + 1 == next_begin && m.count == support &&
            isfinite(values[begin]) && isfinite(values[end])) {
            // A fixed-width slide replaces one observation. This is the stable
            // central-moment update, avoiding two per-mode divisions on ARM.
            double previous_mean = m.mean;
            double difference = values[end] - values[begin];
            m.mean += difference * inverse_support;
            double updated = m.m2 + difference *
                ((values[end] - m.mean) + (values[begin] - previous_mean));
            m.m2 = updated > 0 ? updated : 0;
            begin++; end++;
        } else {
            while (end < next_end) observe(&m, values[end++]);
            while (begin < next_begin) remove_value(&m, values[begin++]);
        }
        if (m.count == window) output[i] = m.m2 * inverse_window;
        else output[i] = full ? NAN : variance(&m);
    }
}

static void rolling_admitted(const double *values, double *output, size_t count,
                             unsigned window, bool lab, bool full) {
    size_t i = 0;
    while (i < count) {
        if (!isfinite(values[i])) { output[i++] = NAN; continue; }
        size_t begin = i;
        while (i < count && isfinite(values[i])) ++i;
        rolling(values + begin, output + begin, i - begin, window, lab, full);
    }
}

int nf_process(nf_processor *p, const float *a, const float *b, size_t count, char *error) {
    return nf_process_at(p, a, b, count, 0, error);
}

static void input_units(nf_processor *p, bool raw) {
    if (p->raw_counts == raw) return;
    bool calibrating = p->calibrating;
    nf_clear_calibration(p);
    p->calibrating = calibrating; p->raw_counts = raw;
}

static int mode_buffers(nf_processor *p, double *q[2], size_t modes) {
    if (p->spare_capacity >= modes) {
        q[0] = p->spare_quadratures[0]; q[1] = p->spare_quadratures[1];
        p->spare_quadratures[0] = p->spare_quadratures[1] = NULL;
        p->spare_capacity = 0;
    } else {
        q[0] = malloc(modes * sizeof(double)); q[1] = malloc(modes * sizeof(double));
    }
    if (q[0] && q[1]) return 0;
    free(q[0]); free(q[1]); q[0] = q[1] = NULL;
    return -1;
}

static void retain_spare(nf_processor *p, double *a, double *b, size_t capacity) {
    free(p->spare_quadratures[0]); free(p->spare_quadratures[1]);
    p->spare_quadratures[0] = a; p->spare_quadratures[1] = b;
    p->spare_capacity = capacity;
}

int nf_process_at(nf_processor *p, const float *a, const float *b, size_t count,
                  uint64_t first_mode, char *error) {
    if (!p->enabled) FAIL("configure the processor first");
    input_units(p, false);
    const nf_config *c = &p->config;
    size_t begin = (size_t)nearbyint(c->guard_seconds * p->sample_rate);
    size_t delay = (size_t)abs(c->b_delay);
    if (count <= begin + c->end_guard + delay) FAIL("capture has no usable samples");
    size_t modes = nf_mode_count(p, count - begin - c->end_guard - delay, first_mode);
    if (!modes) FAIL("capture has no complete temporal mode");
    p->truncated = modes > c->history_modes;
    if (modes > c->history_modes) modes = c->history_modes;
    double *q[2];
    if (mode_buffers(p, q, modes)) FAIL("cannot allocate mode buffer");
    double inverse_rate = 1.0 / p->sample_rate;
    const float *aligned_a = a + begin + (c->b_delay < 0 ? delay : 0);
    const float *aligned_b = b + begin + (c->b_delay > 0 ? delay : 0);
    uint32_t phase = ((first_mode % p->mode_denominator) * p->mode_remainder) % p->mode_denominator;
    size_t base = 0;
    for (size_t k = 0; k < modes; ++k) {
        double sum[2] = {0, 0}; bool valid = true;
        unsigned size = next_mode_size(p, &phase);
        const double *mode = size == p->mode_size ? p->mode : p->mode_large;
        for (unsigned j = 0; j < size; ++j) {
            double va = aligned_a[base + j], vb = aligned_b[base + j];
            if (c->reject_threshold > 0 &&
                (fabs(va) >= c->reject_threshold || fabs(vb) >= c->reject_threshold)) valid = false;
            sum[0] += va * mode[j]; sum[1] += vb * mode[j];
        }
        // Nonfinite inputs propagate through the dot products, even at zero weights.
        valid = valid && isfinite(sum[0]) && isfinite(sum[1]);
        for (unsigned channel = 0; channel < 2; ++channel) {
            q[channel][k] = valid ? sum[channel] * inverse_rate : NAN;
        }
        base += size;
    }
    return nf_process_modes(p, q, modes, error);
}

static int adc_count(uint16_t word) {
    return (int)(word & 8191) - (int)(word & 8192);
}

static inline double weighted_ram(const int16_t *data, size_t count, const double *weights,
                                  double zero, uint16_t *tags) {
    size_t at = 0;
    double sums[4] = {0, 0, 0, 0};
    if (count >= 8) {
        for (; count - at >= 4; at += 4) {
            uint16_t a = data[at], b = data[at + 1], c = data[at + 2], d = data[at + 3];
            if (tags) *tags &= a & b & c & d;
            sums[0] += (adc_count(a) - zero) * weights[at];
            sums[1] += (adc_count(b) - zero) * weights[at + 1];
            sums[2] += (adc_count(c) - zero) * weights[at + 2];
            sums[3] += (adc_count(d) - zero) * weights[at + 3];
        }
    }
    double sum = (sums[0] + sums[1]) + (sums[2] + sums[3]);
    for (; at < count; ++at) {
        uint16_t word = data[at];
        if (tags) *tags &= word;
        sum += (adc_count(word) - zero) * weights[at];
    }
    return sum;
}

static int raw_sum4(const int16_t *data) {
    uint64_t packed;
    const void *source = data;
#if defined(__GNUC__)
    source = __builtin_assume_aligned(source, 8);
#endif
    memcpy(&packed, source, sizeof(packed));
    return adc_count((uint16_t)packed) + adc_count((uint16_t)(packed >> 16)) +
           adc_count((uint16_t)(packed >> 32)) + adc_count((uint16_t)(packed >> 48));
}

static double raw_flat(const int16_t *data, size_t count, double zero, double weight) {
    size_t i = 0; int total = 0;
    for (; i < count && ((uintptr_t)(data + i) & 7); ++i) total += adc_count((uint16_t)data[i]);
#if defined(__ARM_NEON)
    int32x4_t sums = vdupq_n_s32(0);
    for (; count - i >= 8; i += 8) {
        int16x8_t words = vld1q_s16(data + i);
        int16x8_t values = vshrq_n_s16(vshlq_n_s16(words, 2), 2);
        sums = vpadalq_s16(sums, values);
    }
    int32x2_t halves = vadd_s32(vget_low_s32(sums), vget_high_s32(sums));
    total += vget_lane_s32(vpadd_s32(halves, halves), 0);
#endif
    for (; count - i >= 4; i += 4) total += raw_sum4(data + i);
    for (; i < count; ++i) total += adc_count((uint16_t)data[i]);
    return ((double)total - zero * count) * weight;
}

/* ARMv7 has no FP64 SIMD. Direct lane transfers were slower; a 64-byte decode
 * tile amortizes the NEON/VFP transitions for stopped DMA. Keep RAM on the faster
 * scalar path and every weighted addition in its original double order. */
static double raw_weighted(const int16_t *data, size_t count, const double *weights,
                           double zero, bool dma) {
    /* Cortex-A9 has scalar FP64. Independent dependency chains help RAM loads;
     * do not use FP32 NEON or fast-math for scientific weighted projections.
     * DMA and short bins keep their original order below. In four-sample
     * near-degenerate ensemble fixtures, reassociation changes tiny variances
     * enough to exceed the calibrated dB regression tolerance. */
    if (!dma && count >= 8) return weighted_ram(data, count, weights, zero, NULL);
    size_t i = 0;
    double sum = 0;
    for (; i < count && ((uintptr_t)(data + i) & 7); ++i)
        sum += (adc_count((uint16_t)data[i]) - zero) * weights[i];
#if defined(__ARM_NEON)
    if (dma) {
        /* Volatile reads keep the compiler from turning this tile back into
         * costly interleaved NEON lane transfers. No capture-sized copy. */
        volatile int16_t decoded[32];
        while (count - i >= 8) {
            size_t take = (count - i) & ~(size_t)7;
            if (take > 32) take = 32;
            for (size_t lane = 0; lane < take; lane += 8) {
                int16x8_t words = vld1q_s16(data + i + lane);
                int16x8_t values = vshrq_n_s16(vshlq_n_s16(words, 2), 2);
                vst1q_s16((int16_t *)decoded + lane, values);
            }
            for (size_t lane = 0; lane < take; ++lane)
                sum += (decoded[lane] - zero) * weights[i + lane];
            i += take;
        }
    }
#else
    (void)dma;
#endif
    for (; count - i >= 4; i += 4) {
        uint64_t packed;
        const void *source = data + i;
#if defined(__GNUC__)
        source = __builtin_assume_aligned(source, 8);
#endif
        memcpy(&packed, source, sizeof(packed));
#if defined(__BYTE_ORDER__) && __BYTE_ORDER__ == __ORDER_BIG_ENDIAN__
        sum += (adc_count((uint16_t)(packed >> 48)) - zero) * weights[i];
        sum += (adc_count((uint16_t)(packed >> 32)) - zero) * weights[i + 1];
        sum += (adc_count((uint16_t)(packed >> 16)) - zero) * weights[i + 2];
        sum += (adc_count((uint16_t)packed) - zero) * weights[i + 3];
#else
        sum += (adc_count((uint16_t)packed) - zero) * weights[i];
        sum += (adc_count((uint16_t)(packed >> 16)) - zero) * weights[i + 1];
        sum += (adc_count((uint16_t)(packed >> 32)) - zero) * weights[i + 2];
        sum += (adc_count((uint16_t)(packed >> 48)) - zero) * weights[i + 3];
#endif
    }
    for (; i < count; ++i) sum += (adc_count((uint16_t)data[i]) - zero) * weights[i];
    return sum;
}

static double raw_chunk(const int16_t *data, size_t count, const double *weights,
                         bool flat, double zero, const float *volts, double limit, bool *valid, bool dma) {
    if (flat && limit == 0) return raw_flat(data, count, zero, weights[0]);
    if (!flat && limit == 0) return raw_weighted(data, count, weights, zero, dma);
    size_t i = 0; int total = 0; double sum = 0;
    /* Equal weights admit an exact integer sum and one multiplication per bin. */
#define ADD_WORD(word, index) do { \
    uint16_t code = (uint16_t)(word); int value = adc_count(code); \
    if (flat) total += value; else sum += (value - zero) * weights[index]; \
    if (limit > 0 && fabs(volts[code & 16383]) >= limit) *valid = false; \
} while (0)
    for (; i < count && ((uintptr_t)(data + i) & 7); ++i) ADD_WORD(data[i], i);
    for (; count - i >= 4; i += 4) {
        uint64_t packed;
        const void *source = data + i;
#if defined(__GNUC__)
        source = __builtin_assume_aligned(source, 8);
#endif
        memcpy(&packed, source, sizeof(packed));
#if defined(__BYTE_ORDER__) && __BYTE_ORDER__ == __ORDER_BIG_ENDIAN__
        ADD_WORD(packed >> 48, i); ADD_WORD(packed >> 32, i + 1);
        ADD_WORD(packed >> 16, i + 2); ADD_WORD(packed, i + 3);
#else
        ADD_WORD(packed, i); ADD_WORD(packed >> 16, i + 1);
        ADD_WORD(packed >> 32, i + 2); ADD_WORD(packed >> 48, i + 3);
#endif
    }
    for (; i < count; ++i) ADD_WORD(data[i], i);
#undef ADD_WORD
    return flat ? ((double)total - zero * count) * weights[0] : sum;
}

static int project_raw(nf_processor *p, const nf_raw_channel channels[2], size_t count,
                        double *q[2], size_t *mode_count, char *error, bool dma) {
    q[0] = q[1] = NULL; *mode_count = 0;
    if (!p->enabled) FAIL("configure the processor first");
    const nf_config *c = &p->config;
    for (unsigned ch = 0; ch < 2; ++ch) {
        const nf_raw_channel *r = &channels[ch];
        if (r->size[0] > count || r->size[1] != count - r->size[0] ||
            (r->size[0] && !r->data[0]) || (r->size[1] && !r->data[1]) ||
            !isfinite(r->zero) || (c->reject_threshold > 0 && !r->volts))
            FAIL("invalid raw ADC regions or calibration");
    }
    input_units(p, true);
    size_t begin = (size_t)nearbyint(c->guard_seconds * p->sample_rate);
    size_t delay = (size_t)abs(c->b_delay);
    if (count <= begin + c->end_guard + delay) FAIL("capture has no usable samples");
    size_t modes = nf_mode_count(p, count - begin - c->end_guard - delay, 0);
    if (!modes) FAIL("capture has no complete temporal mode");
    p->truncated = modes > c->history_modes;
    if (modes > c->history_modes) modes = c->history_modes;
    if (mode_buffers(p, q, modes)) FAIL("cannot allocate mode buffer");
    bool flat = !c->template_count && !c->sampled_counts[0] && !strcmp(c->shape, "identity");
    double inverse_rate = 1.0 / p->sample_rate;
    for (size_t k = 0; k < modes; ++k) {
        size_t base = p->raw_boundaries[k];
        unsigned size = p->raw_boundaries[k + 1] - (unsigned)base;
        const double *weights = size == p->mode_size ? p->mode : p->mode_large;
        bool valid = true;
        for (unsigned ch = 0; ch < 2; ++ch) {
            const nf_raw_channel *r = &channels[ch];
            size_t pos = begin + base + ((ch == 0 ? c->b_delay < 0 : c->b_delay > 0) ? delay : 0);
            unsigned region = pos < r->size[0] ? 0 : 1;
            if (region) pos -= r->size[0];
            size_t used = 0; double sum = 0;
            while (used < size) {
                size_t take = r->size[region] - pos;
                if (take > size - used) take = size - used;
                sum += raw_chunk(r->data[region] + pos, take, weights + used, flat, r->zero,
                                 r->volts, c->reject_threshold, &valid, dma);
                used += take; region++; pos = 0;
            }
            q[ch][k] = sum * inverse_rate;
        }
        if (!valid) q[0][k] = q[1][k] = NAN;
    }
    *mode_count = modes;
    return 0;
}

int nf_project_raw(nf_processor *p, const nf_raw_channel channels[2], size_t count,
                   double *q[2], size_t *modes, char *error) {
    return project_raw(p, channels, count, q, modes, error, false);
}

int nf_project_raw_dma(nf_processor *p, const nf_raw_channel channels[2], size_t count,
                       double *q[2], size_t *modes, char *error) {
    return project_raw(p, channels, count, q, modes, error, true);
}

#define TAGGED_ADMITTED 0xc000u

/* Return marker/all-LOW flags. Invert only the gate bit before the AND reduction:
 * any HIGH sample then clears admission, including a mixed HIGH/LOW mode.
 * Copied ADC words and their physical E1 tags stay unchanged. */
static uint16_t tagged_copy(const int16_t *data, size_t count, int16_t *out) {
    uint16_t tags = TAGGED_ADMITTED;
    size_t i = 0;
#if defined(__ARM_NEON)
    uint16x8_t all = vdupq_n_u16(TAGGED_ADMITTED);
    const uint16x8_t gate = vdupq_n_u16(0x4000u);
    for (; count - i >= 8; i += 8) {
        int16x8_t words = vld1q_s16(data + i);
        all = vandq_u16(all, veorq_u16(vreinterpretq_u16_s16(words), gate));
        if (out) vst1q_s16(out + i, words);
    }
    uint16_t lanes[8];
    vst1q_u16(lanes, all);
    for (size_t lane = 0; lane < 8; ++lane) tags &= lanes[lane];
#endif
    for (; i < count; ++i) {
        int16_t word = data[i];
        tags &= (uint16_t)word ^ 0x4000u;
        if (out) out[i] = word;
    }
    return tags;
}

static uint16_t tagged_span(const nf_raw_channel *raw, size_t start, size_t count, int16_t *out) {
    if (!count) return TAGGED_ADMITTED;
    if (start >= raw->size[0])
        return tagged_copy(raw->data[1] + start - raw->size[0], count, out);
    size_t first = raw->size[0] - start;
    if (first >= count) return tagged_copy(raw->data[0] + start, count, out);
    uint16_t tags = tagged_copy(raw->data[0] + start, first, out);
    return tags & tagged_copy(raw->data[1], count - first, out ? out + first : NULL);
}

int nf_validate_tagged_pair(const nf_raw_channel raw[2], char *error) {
    size_t total = raw[0].size[0] + raw[0].size[1];
    if (total != raw[1].size[0] + raw[1].size[1]) FAIL("packed ADC rail lengths disagree");
    size_t region[2] = {0, 0}, offset[2] = {0, 0}, done = 0;
    while (done < total) {
        for (unsigned ch = 0; ch < 2; ++ch)
            if (offset[ch] == raw[ch].size[region[ch]]) { region[ch]++; offset[ch] = 0; }
        size_t count = raw[0].size[region[0]] - offset[0];
        size_t other = raw[1].size[region[1]] - offset[1];
        if (other < count) count = other;
        const uint16_t *a = (const uint16_t *)(raw[0].data[region[0]] + offset[0]);
        const uint16_t *b = (const uint16_t *)(raw[1].data[region[1]] + offset[1]);
        size_t i = 0; uint16_t missing = 0, different = 0;
#if defined(__ARM_NEON)
        uint16x8_t markers = vdupq_n_u16(0x8000u), differences = vdupq_n_u16(0);
        for (; count - i >= 8; i += 8) {
            uint16x8_t av = vld1q_u16(a + i), bv = vld1q_u16(b + i);
            markers = vandq_u16(markers, vandq_u16(av, bv));
            differences = vorrq_u16(differences, veorq_u16(av, bv));
        }
        uint16_t mark[8], diff[8]; vst1q_u16(mark, markers); vst1q_u16(diff, differences);
        for (unsigned lane = 0; lane < 8; ++lane) {
            missing |= (uint16_t)(~mark[lane] & 0x8000u); different |= diff[lane] & 0x4000u;
        }
#endif
        for (; i < count; ++i) {
            missing |= (uint16_t)(~(a[i] & b[i]) & 0x8000u);
            different |= (a[i] ^ b[i]) & 0x4000u;
        }
        if (missing) FAIL("invalid packed ADC format marker; capture discarded");
        if (different) FAIL("packed ADC E1 tags disagree; capture discarded");
        for (unsigned ch = 0; ch < 2; ++ch) offset[ch] += count;
        done += count;
    }
    return 0;
}

static size_t tagged_run_end(const nf_raw_channel *raw, size_t start, size_t end, uint16_t level) {
    while (start < end) {
        unsigned region = start >= raw->size[0];
        size_t offset = region ? start - raw->size[0] : start;
        size_t count = raw->size[region] - offset;
        if (count > end - start) count = end - start;
        const int16_t *data = raw->data[region] + offset;
        size_t i = 0;
#if defined(__ARM_NEON)
        uint16x8_t mask = vdupq_n_u16(0x4000u), e1 = vdupq_n_u16(level);
        for (; count - i >= 8; i += 8) {
            uint16x8_t words = vreinterpretq_u16_s16(vld1q_s16(data + i));
            uint64x2_t differences = vreinterpretq_u64_u16(vandq_u16(veorq_u16(words, e1), mask));
            if (vgetq_lane_u64(differences, 0) | vgetq_lane_u64(differences, 1)) break;
        }
#endif
        for (; i < count; ++i)
            if (((uint16_t)data[i] & 0x4000u) != level) return start + i;
        start += count;
    }
    return end;
}

int nf_prepare_tagged(nf_processor *p, const nf_raw_channel raw[2], size_t measurement,
                      nf_tagged_plan *plan, double *q[2], char *error) {
    if (!p->enabled) FAIL("configure the processor first");
    const nf_config *c = &p->config;
    size_t begin = (size_t)nearbyint(c->guard_seconds * p->sample_rate);
    size_t delay = (size_t)abs(c->b_delay);
    if (measurement > raw[0].size[0] + raw[0].size[1]) FAIL("measurement exceeds packed ADC data");
    if (measurement <= begin + c->end_guard + delay) FAIL("capture has no usable samples");
    size_t end = measurement - c->end_guard;
    size_t modes = nf_mode_count(p, end - begin - delay, 0);
    if (!modes) FAIL("capture has no complete temporal mode");
    *plan = (nf_tagged_plan){{begin + (c->b_delay < 0 ? delay : 0),
                             begin + (c->b_delay > 0 ? delay : 0)},
                            modes > c->history_modes ? c->history_modes : modes,
                            modes > c->history_modes, NULL};
    /* Shot-noise calibration uses the unbroken capture clock on both E1 levels. */
    if (!p->calibrating) {
        if (p->tagged_capacity < c->history_modes) {
            uint32_t *bins = realloc(p->tagged_bins, c->history_modes * sizeof(*bins));
            if (!bins) FAIL("cannot allocate tagged mode boundaries");
            p->tagged_bins = bins;
            p->tagged_capacity = c->history_modes;
        }
        uint32_t *bins = p->tagged_bins;
        memset(bins, 0xff, plan->modes * sizeof(*bins));
        size_t used = 0, initialized = plan->modes, start = begin;
        uint16_t level = (uint16_t)(start < raw[0].size[0] ? raw[0].data[0][start] :
                                    raw[0].data[1][start - raw[0].size[0]]) & 0x4000u;
        while (start < end) {
            size_t stop = tagged_run_end(&raw[0], start, end, level);
            if (!level && stop - start > delay) {
                size_t low_modes = nf_mode_count(p, stop - start - delay, 0);
                if (low_modes) {
                    /* Retain time slots through HIGH and always leave a gap
                     * between LOW runs so a formula cannot cross the edge. */
                    size_t slot = nf_mode_count(p, start - begin, 0);
                    if (used && slot <= used) slot = used + 1;
                    if (slot >= c->history_modes) {
                        plan->truncated = true;
                        break;
                    }
                    if (slot > initialized)
                        memset(bins + initialized, 0xff, (slot - initialized) * sizeof(*bins));
                    if (low_modes > c->history_modes - slot) {
                        low_modes = c->history_modes - slot;
                        plan->truncated = true;
                    }
                    uint32_t base = (uint32_t)(start - begin);
                    for (size_t i = 0; i < low_modes; ++i) {
                        bool long_bin = p->raw_boundaries[i + 1] - p->raw_boundaries[i] > p->mode_size;
                        bins[slot + i] = (base + p->raw_boundaries[i]) |
                                         (long_bin ? NF_TAGGED_LONG_BIN : 0);
                    }
                    used = slot + low_modes;
                    if (used > initialized) initialized = used;
                }
            }
            start = stop;
            level ^= 0x4000u;
        }
        if (used > plan->modes) plan->modes = used;
        plan->bins = bins;
    }
    if (mode_buffers(p, q, plan->modes)) FAIL("cannot allocate mode buffer");
    return 0;
}

static uint32_t tagged_bin(const nf_processor *p, const nf_tagged_plan *plan, size_t index) {
    if (plan->bins) return plan->bins[index];
    bool long_bin = p->raw_boundaries[index + 1] - p->raw_boundaries[index] > p->mode_size;
    return p->raw_boundaries[index] | (long_bin ? NF_TAGGED_LONG_BIN : 0);
}

uint16_t nf_project_tagged_rail(const nf_processor *p, const nf_raw_channel *raw,
                               const nf_tagged_plan *plan, unsigned channel, int16_t *tile,
                               size_t tile_words, double *q) {
    size_t start = plan->start[channel];
    uint16_t tags = TAGGED_ADMITTED;
    bool flat = !p->config.template_count && !p->config.sampled_counts[0] &&
                !strcmp(p->config.shape, "identity");
    double inverse_rate = 1.0 / p->sample_rate;
    size_t k = 0;
    while (k < plan->modes && (tags & 0x8000u)) {
        uint32_t bin = tagged_bin(p, plan, k);
        if (bin == NF_TAGGED_GAP) { q[k++] = NAN; continue; }
        size_t first = bin & NF_TAGGED_OFFSET_MASK, next = k + 1;
        size_t end = first + p->mode_size + !!(bin & NF_TAGGED_LONG_BIN);
        bool tiled = tile && end - first <= tile_words;
        if (tiled) {
            while (next < plan->modes) {
                bin = tagged_bin(p, plan, next);
                if ((bin & NF_TAGGED_OFFSET_MASK) != end) break;
                size_t size = p->mode_size + !!(bin & NF_TAGGED_LONG_BIN);
                if (end + size - first > tile_words) break;
                end += size;
                ++next;
            }
            tags &= tagged_span(raw, start + first, end - first, tile);
            if (!(tags & 0x8000u)) break;
        }
        for (; k < next; ++k) {
            bin = tagged_bin(p, plan, k);
            size_t base = bin & NF_TAGGED_OFFSET_MASK;
            size_t count = p->mode_size + !!(bin & NF_TAGGED_LONG_BIN);
            const double *weights = count == p->mode_size ? p->mode : p->mode_large;
            const int16_t *data;
            int16_t wrapped[NF_MAX_TEMPLATE];
            size_t pos = start + base;
            if (tiled) data = tile + base - first;
            else if (pos >= raw->size[0]) data = raw->data[1] + pos - raw->size[0];
            else if (count <= raw->size[0] - pos) data = raw->data[0] + pos;
            else {
                tagged_span(raw, pos, count, wrapped);
                data = wrapped;
            }
            if (!tiled) tags &= tagged_copy(data, count, NULL);
            double value = flat ? raw_flat(data, count, raw->zero, weights[0]) :
                                 raw_weighted(data, count, weights, raw->zero, false);
            q[k] = value * inverse_rate;
        }
    }
    return tags;
}

void nf_finish_tagged(nf_processor *p, const nf_tagged_plan *plan, double *q[2], bool accepted) {
    if (accepted) {
        input_units(p, true);
        p->truncated = plan->truncated;
        p->tagged_capture = true; p->captured_modes = plan->modes;
        p->included_modes = p->excluded_modes = 0;
        for (size_t k = 0; k < plan->modes; ++k) {
            if (isfinite(q[0][k]) && isfinite(q[1][k])) p->included_modes++;
            else { q[0][k] = q[1][k] = NAN; p->excluded_modes++; }
        }
    } else {
        retain_spare(p, q[0], q[1], plan->modes);
        q[0] = q[1] = NULL;
    }
}

/* Preserve the exact ratio and logarithmic statistic. Undefined/zero/infinite
 * ratios have known results and need no libm call, especially at masked edges. */
static double squeezing_db(double value, double ref, uint64_t *calls) {
    double ratio = value / ref;
    if (!(ratio > 0)) return ratio == 0 ? -INFINITY : NAN;
    if (isinf(ratio)) return INFINITY;
    (*calls)++;
    return 10 * log10(ratio);
}

static void evaluate_formula(double *values, double *const q[2], size_t size,
                             const nf_row *formula) {
    const nf_term *t = formula->terms;
    bool four = formula->term_count == 4 && t[0].weight == 1 && t[2].weight == 1 &&
        ((t[1].weight == 1 && t[3].weight == -1) ||
         (t[1].weight == -1 && t[3].weight == 1));
    bool eight = formula->term_count == 8 && t[0].weight == 1 && t[1].weight == 1 &&
        t[4].weight == -1 && t[5].weight == 1 &&
        ((t[2].weight == -1 && t[3].weight == -1 && t[6].weight == -1 && t[7].weight == 1) ||
         (t[2].weight == 1 && t[3].weight == 1 && t[6].weight == 1 && t[7].weight == -1));
    if (four || eight) {
        const double *source[8];
        for (unsigned j = 0; j < formula->term_count; ++j)
            source[j] = q[t[j].source] + formula->start + t[j].offset;
        // A single output pass retains every configured source/offset and the
        // original sequential term order, including the initial +0 addition.
        if (four && t[1].weight == 1) {
            for (size_t i = 0; i < size; ++i) {
                double value = 0.0 + source[0][i];
                value += source[1][i]; value += source[2][i]; value -= source[3][i];
                values[i] = value;
            }
        } else if (four) {
            for (size_t i = 0; i < size; ++i) {
                double value = 0.0 + source[0][i];
                value -= source[1][i]; value += source[2][i]; value += source[3][i];
                values[i] = value;
            }
        } else if (t[2].weight == -1) {
            for (size_t i = 0; i < size; ++i) {
                double value = 0.0 + source[0][i];
                value += source[1][i]; value -= source[2][i]; value -= source[3][i];
                value -= source[4][i]; value += source[5][i]; value -= source[6][i];
                value += source[7][i]; values[i] = value;
            }
        } else {
            for (size_t i = 0; i < size; ++i) {
                double value = 0.0 + source[0][i];
                value += source[1][i]; value += source[2][i]; value += source[3][i];
                value -= source[4][i]; value += source[5][i]; value += source[6][i];
                value -= source[7][i]; values[i] = value;
            }
        }
        return;
    }
    bool first = true;
    // Keep the configured term addition order, including duplicate terms.
    for (unsigned j = 0; j < formula->term_count; ++j) {
        const nf_term *term = &formula->terms[j];
        if (!term->weight) continue; // A zero-weight NaN never invalidates a row.
        const double *source = q[term->source] + formula->start + term->offset;
        if (first) {
            // Retain the original initial +0 addition (including signed zero).
            for (size_t i = 0; i < size; ++i) values[i] = 0.0 + term->weight * source[i];
            first = false;
        } else if (term->weight == 1) {
            for (size_t i = 0; i < size; ++i) values[i] += source[i];
        } else if (term->weight == -1) {
            for (size_t i = 0; i < size; ++i) values[i] -= source[i];
        } else {
            for (size_t i = 0; i < size; ++i) values[i] += term->weight * source[i];
        }
    }
}

int nf_process_modes(nf_processor *p, double *q[2], size_t modes, char *error) {
    double started = process_clock_ms();
    memset(&p->process_timing, 0, sizeof(p->process_timing));
    const nf_config *c = &p->config;
    if (!p->enabled || !modes || modes > c->history_modes || !q[0] || !q[1]) {
        p->process_timing.total_ms = process_clock_ms() - started;
        free(q[0]); free(q[1]); FAIL("invalid projected temporal modes");
    }
    p->captures++;
    if (p->calibrating) {
        for (unsigned ch = 0; ch < 2; ++ch) {
            nf_moments segment = {0};
            for (size_t k = 0; k < modes; ++k) {
                if (p->tagged_capture && !isfinite(q[ch][k])) {
                    observe(&p->segment_variances[ch], variance(&segment));
                    memset(&segment, 0, sizeof(segment));
                    continue;
                }
                observe(&p->calibration[ch], q[ch][k]); observe(&segment, q[ch][k]);
            }
            observe(&p->segment_variances[ch], variance(&segment));
        }
        p->process_timing.normalization_ms = process_clock_ms() - started;
        p->process_timing.total_ms = p->process_timing.normalization_ms;
        retain_spare(p, q[0], q[1], modes); return 0;
    }
    for (unsigned ch = 0; ch < 2; ++ch) {
        double coefficient = p->coefficients[ch];
        if (coefficient != 1)
            for (size_t k = 0; k < modes; ++k) q[ch][k] *= coefficient;
    }
    double checkpoint = process_clock_ms();
    p->process_timing.normalization_ms = checkpoint - started;
    bool lab = strcmp(c->estimator, "lab_rolling") == 0;
    bool ensemble = strcmp(c->estimator, "ensemble") == 0;
    double requested = c->variance_seconds * p->sample_rate / p->mode_step;
    unsigned window = c->variance_modes ? c->variance_modes :
        (unsigned)fmin(c->history_modes, fmax(lab ? floor(requested) : nearbyint(requested), lab ? 10 : 2));
    for (unsigned index = 0; index < c->row_count; ++index) {
        const nf_row *formula = &c->rows[index]; nf_row_state *r = &p->rows[index];
        if (allocate_row(r, c->history_modes, lab, ensemble)) {
            p->process_timing.total_ms = process_clock_ms() - started;
            free(q[0]); free(q[1]); nf_clear_results(p); FAIL("cannot allocate formula history");
        }
        unsigned offset = 0;
        for (unsigned j = 0; j < formula->term_count; ++j)
            if (formula->terms[j].offset > offset) offset = formula->terms[j].offset;
        size_t size = modes > offset + formula->start ? modes - offset - formula->start : 0;
        size_t retained = size > r->size ? size : r->size;
        if (size) evaluate_formula(r->values, q, size, formula);
        if (size && p->tagged_capture && p->excluded_modes) {
            size_t missing = 0, begin = formula->start;
            for (size_t j = 0; j <= offset; ++j) missing += !isfinite(q[0][begin + j]);
            for (size_t i = 0; i < size; ++i) {
                if (missing) r->values[i] = NAN;
                if (i + 1 < size) {
                    missing -= !isfinite(q[0][begin + i]);
                    missing += !isfinite(q[0][begin + i + offset + 1]);
                }
            }
        }
        double now = process_clock_ms();
        p->process_timing.formulas_ms += now - checkpoint; checkpoint = now;
        if (!ensemble) {
            if (p->tagged_capture && p->excluded_modes)
                rolling_admitted(r->values, r->variance, size, window, lab, c->require_full_variance);
            else rolling(r->values, r->variance, size, window, lab, c->require_full_variance);
        }
        now = process_clock_ms();
        p->process_timing.rolling_ms += now - checkpoint; checkpoint = now;
        double ref = reference(formula);
        if (ensemble) {
            for (size_t i = 0; i < size; ++i) {
                if (p->tagged_capture && !isfinite(r->values[i])) {
                    r->variance[i] = r->db[i] = NAN; continue;
                }
                observe(&r->ensemble[i], r->values[i]); r->counts[i] = r->ensemble[i].count;
                r->variance[i] = variance(&r->ensemble[i]);
                r->db[i] = p->calibrated ?
                    squeezing_db(r->variance[i], ref, &p->process_timing.log10_calls) : NAN;
            }
        } else if (lab) {
            for (size_t i = 0; i < size; ++i) {
                if (p->tagged_capture && !isfinite(r->values[i])) {
                    r->variance[i] = r->db[i] = NAN; continue;
                }
                double v = r->variance[i];
                if (!isnan(v)) {
                    r->variance_sum[i] += v; r->counts[i]++;
                    if (p->calibrated) r->db_sum[i] +=
                        squeezing_db(v, ref, &p->process_timing.log10_calls);
                }
                r->variance[i] = r->counts[i] ? r->variance_sum[i] / (double)r->counts[i] : NAN;
                r->db[i] = p->calibrated && r->counts[i] ?
                    r->db_sum[i] / (double)r->counts[i] : NAN;
            }
        } else if (p->calibrated) {
            for (size_t i = 0; i < size; ++i) {
                r->counts[i] = isfinite(r->variance[i]) ? 1 : 0;
                r->db[i] = squeezing_db(r->variance[i], ref, &p->process_timing.log10_calls);
            }
        } else {
            for (size_t i = 0; i < size; ++i) {
                r->counts[i] = isfinite(r->variance[i]) ? 1 : 0;
                r->db[i] = NAN;
            }
        }
        for (size_t i = size; i < retained; ++i) r->values[i] = NAN;
        r->size = (lab || ensemble) ? retained : size;
        now = process_clock_ms();
        p->process_timing.statistics_ms += now - checkpoint; checkpoint = now;
    }
    retain_spare(p, p->quadratures[0], p->quadratures[1], p->quadrature_count);
    p->quadratures[0] = q[0]; p->quadratures[1] = q[1]; p->quadrature_count = modes;
    p->has_result = true;
    p->process_timing.total_ms = process_clock_ms() - started;
    return 0;
}

void nf_destroy(nf_processor *p) {
    nf_clear_results(p);
    free(p->tagged_bins);
    p->tagged_bins = NULL; p->tagged_capacity = 0;
}
