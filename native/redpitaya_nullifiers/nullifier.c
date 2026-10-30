#include "nullifier.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

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
    c->decimation = 1; c->capture_samples = 625024; c->history_modes = NF_MAX_HISTORY;
    c->min_samples = 4; c->oversample = 1000; c->b_delay = 2;
    c->mode_width = 308e-9; c->frequency = 6e6; c->variance_seconds = 1.0 / 3000;
    c->guard_seconds = 0.0008006; c->end_guard = 1000; c->timeout_seconds = 5;
    c->lock_seconds = 0.005; c->collect_seconds = 0.005; strcpy(c->window_mode, "periodic");
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

static int make_mode(nf_processor *p, char *error) {
    const nf_config *c = &p->config;
    double dt = 1 / p->sample_rate;
    bool lab = strcmp(c->shape, "lab_gaussian_linear_cosine") == 0;
    unsigned n = c->template_count ? c->template_count : c->mode_samples;
    double width = c->mode_samples ? c->mode_samples * dt : c->mode_width;
    if (!n) {
        double points = width / dt;
        if (points > NF_MAX_TEMPLATE) FAIL("mode exceeds 4096 samples");
        n = (unsigned)(lab ? floor(nextafter(points, INFINITY)) : nearbyint(points));
    }
    if (n < c->min_samples || n > NF_MAX_TEMPLATE) FAIL("invalid samples per temporal mode");
    p->mode_size = n;
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

void nf_clear_results(nf_processor *p) {
    free(p->quadratures[0]); free(p->quadratures[1]);
    p->quadratures[0] = p->quadratures[1] = NULL; p->quadrature_count = 0;
    for (unsigned i = 0; i < NF_MAX_ROWS; ++i) {
        nf_row_state *r = &p->rows[i];
        free(r->values); free(r->variance); free(r->db); free(r->ensemble);
        free(r->variance_sum); free(r->db_sum); free(r->counts);
        memset(r, 0, sizeof(*r));
    }
    p->has_result = false; p->captures = 0; p->truncated = false;
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

int nf_configure(nf_processor *p, const nf_config *c, char *error) {
    if (!c->decimation || c->decimation > 65536 ||
        (c->decimation < 16 && c->decimation != 1 && c->decimation != 2 &&
         c->decimation != 4 && c->decimation != 8)) FAIL("unsupported ADC decimation");
    if (!c->capture_samples || c->capture_samples > 4194304 || c->capture_samples % 64)
        FAIL("captureSamples must be a multiple of 64 between 64 and 4194304");
    if (c->history_modes < 100 || c->history_modes > NF_MAX_HISTORY ||
        c->min_samples < 2 || c->min_samples > NF_MAX_TEMPLATE || !c->oversample ||
        c->oversample > 1000 || c->mode_samples > NF_MAX_TEMPLATE ||
        c->variance_modes > c->history_modes || (c->variance_modes && c->variance_modes < 2))
        FAIL("processing count exceeds its supported range");
    if (!(c->mode_width > 0) || !(c->frequency > 0) || !(c->variance_seconds > 0) ||
        c->guard_seconds < 0 || !(c->timeout_seconds > 0) || c->timeout_seconds > 60 ||
        c->reject_threshold < 0 || abs(c->b_delay) > 65536) FAIL("invalid timing or amplitude setting");
    if (strcmp(c->shape, "identity") && strcmp(c->shape, "gaussian_linear") &&
        strcmp(c->shape, "lab_gaussian_linear_cosine")) FAIL("unsupported modeShape");
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
    int result = make_mode(candidate, error);
    double guard = nearbyint(c->guard_seconds * candidate->sample_rate);
    if (!result && (guard + c->end_guard + abs(c->b_delay) + candidate->mode_size > c->capture_samples)) {
        snprintf(error, NF_ERROR_SIZE, "capture guards and delay leave no complete temporal mode");
        result = -1;
    }
    if (!result && !strcmp(c->window_mode, "periodic")) {
        double samples = nearbyint(c->collect_seconds * candidate->sample_rate);
        if (samples > 4194304 || guard + c->end_guard + abs(c->b_delay) + candidate->mode_size > samples) {
            snprintf(error, NF_ERROR_SIZE, "streaming collection guards leave no complete mode or exceed memory limit");
            result = -1;
        }
    }
    if (!result) {
        nf_clear_calibration(p); p->config = *c; p->sample_rate = candidate->sample_rate;
        p->mode_size = candidate->mode_size; memcpy(p->mode, candidate->mode, sizeof(p->mode));
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

static int allocate_row(nf_row_state *r, size_t capacity) {
    if (r->values) return 0;
    r->values = calloc(capacity, sizeof(double)); r->variance = calloc(capacity, sizeof(double));
    r->db = calloc(capacity, sizeof(double)); r->ensemble = calloc(capacity, sizeof(nf_moments));
    r->variance_sum = calloc(capacity, sizeof(double)); r->db_sum = calloc(capacity, sizeof(double));
    r->counts = calloc(capacity, sizeof(uint64_t));
    return r->values && r->variance && r->db && r->ensemble && r->variance_sum &&
           r->db_sum && r->counts ? 0 : -1;
}

static void rolling(const double *values, double *output, size_t count, unsigned window, bool lab, bool full) {
    size_t left = window / 2, right = lab ? left : window - left;
    size_t begin = 0, end = 0;
    nf_moments m = {0};
    double inverse_window = 1.0 / window;
    for (size_t i = 0; i < count; ++i) {
        size_t next_begin = i > left ? i - left : 0;
        size_t next_end = i + right < count ? i + right : count;
        if (end + 1 == next_end && begin + 1 == next_begin && m.count == window &&
            isfinite(values[begin]) && isfinite(values[end])) {
            // A fixed-width slide replaces one observation. This is the stable
            // central-moment update, avoiding two per-mode divisions on ARM.
            double previous_mean = m.mean;
            double difference = values[end] - values[begin];
            m.mean += difference * inverse_window;
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

int nf_process(nf_processor *p, const float *a, const float *b, size_t count, char *error) {
    if (!p->enabled) FAIL("configure the processor first");
    const nf_config *c = &p->config;
    size_t begin = (size_t)nearbyint(c->guard_seconds * p->sample_rate);
    size_t delay = (size_t)abs(c->b_delay);
    if (count <= begin + c->end_guard + delay) FAIL("capture has no usable samples");
    size_t modes = (count - begin - c->end_guard - delay) / p->mode_size;
    if (!modes) FAIL("capture has no complete temporal mode");
    p->truncated = modes > c->history_modes;
    if (modes > c->history_modes) modes = c->history_modes;
    double *q[2] = {malloc(modes * sizeof(double)), malloc(modes * sizeof(double))};
    if (!q[0] || !q[1]) { free(q[0]); free(q[1]); FAIL("cannot allocate mode buffer"); }
    nf_moments segments[2] = {{0}, {0}};
    double inverse_rate = 1.0 / p->sample_rate;
    const float *aligned_a = a + begin + (c->b_delay < 0 ? delay : 0);
    const float *aligned_b = b + begin + (c->b_delay > 0 ? delay : 0);
    for (size_t k = 0; k < modes; ++k) {
        double sum[2] = {0, 0}; bool valid = true;
        size_t base = k * p->mode_size;
        for (unsigned j = 0; j < p->mode_size; ++j) {
            double va = aligned_a[base + j], vb = aligned_b[base + j];
            if (c->reject_threshold > 0 &&
                (fabs(va) >= c->reject_threshold || fabs(vb) >= c->reject_threshold)) valid = false;
            sum[0] += va * p->mode[j]; sum[1] += vb * p->mode[j];
        }
        // Nonfinite inputs propagate through the dot products, even at zero weights.
        valid = valid && isfinite(sum[0]) && isfinite(sum[1]);
        for (unsigned channel = 0; channel < 2; ++channel) {
            q[channel][k] = valid ? sum[channel] * inverse_rate : NAN;
            if (p->calibrating) {
                observe(&p->calibration[channel], q[channel][k]);
                observe(&segments[channel], q[channel][k]);
            } else q[channel][k] *= p->coefficients[channel];
        }
    }
    p->captures++;
    if (p->calibrating) {
        for (unsigned channel = 0; channel < 2; ++channel)
            observe(&p->segment_variances[channel], variance(&segments[channel]));
        free(q[0]); free(q[1]); return 0;
    }
    bool lab = strcmp(c->estimator, "lab_rolling") == 0;
    bool ensemble = strcmp(c->estimator, "ensemble") == 0;
    double requested = c->variance_seconds * p->sample_rate / p->mode_size;
    unsigned window = c->variance_modes ? c->variance_modes :
        (unsigned)fmin(c->history_modes, fmax(lab ? floor(requested) : nearbyint(requested), lab ? 10 : 2));
    for (unsigned index = 0; index < c->row_count; ++index) {
        const nf_row *formula = &c->rows[index]; nf_row_state *r = &p->rows[index];
        if (allocate_row(r, c->history_modes)) {
            free(q[0]); free(q[1]); nf_clear_results(p); FAIL("cannot allocate formula history");
        }
        unsigned offset = 0;
        for (unsigned j = 0; j < formula->term_count; ++j)
            if (formula->terms[j].offset > offset) offset = formula->terms[j].offset;
        size_t size = modes > offset + formula->start ? modes - offset - formula->start : 0;
        size_t retained = size > r->size ? size : r->size;
        memset(r->values, 0, size * sizeof(double));
        // Each term reads a contiguous range, retaining the same addition order
        // per output while allowing the compiler to vectorize across modes.
        for (unsigned j = 0; j < formula->term_count; ++j) {
            const nf_term *t = &formula->terms[j];
            if (!t->weight || !size) continue;
            const double *source = q[t->source] + formula->start + t->offset;
            for (size_t i = 0; i < size; ++i) r->values[i] += t->weight * source[i];
        }
        if (!ensemble) rolling(r->values, r->variance, size, window, lab, c->require_full_variance);
        double ref = reference(formula);
        for (size_t i = 0; i < size; ++i) {
            if (ensemble) {
                observe(&r->ensemble[i], r->values[i]); r->counts[i] = r->ensemble[i].count;
                r->variance[i] = variance(&r->ensemble[i]);
                r->db[i] = p->calibrated ? 10 * log10(r->variance[i] / ref) : NAN;
            } else if (lab) {
                double v = r->variance[i];
                if (!isnan(v)) {
                    r->variance_sum[i] += v; r->counts[i]++;
                    if (p->calibrated) r->db_sum[i] += 10 * log10(v / ref);
                }
                r->variance[i] = r->counts[i] ? r->variance_sum[i] / (double)r->counts[i] : NAN;
                r->db[i] = p->calibrated && r->counts[i] ?
                    r->db_sum[i] / (double)r->counts[i] : NAN;
            } else {
                r->counts[i] = isfinite(r->variance[i]) ? 1 : 0;
                r->db[i] = p->calibrated ? 10 * log10(r->variance[i] / ref) : NAN;
            }
        }
        for (size_t i = size; i < retained; ++i) r->values[i] = NAN;
        r->size = (lab || ensemble) ? retained : size;
    }
    free(p->quadratures[0]); free(p->quadratures[1]);
    p->quadratures[0] = q[0]; p->quadratures[1] = q[1]; p->quadrature_count = modes;
    p->has_result = true;
    return 0;
}

void nf_destroy(nf_processor *p) { nf_clear_results(p); }
