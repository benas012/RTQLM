/* Larsen thesis Chapter 3, Eqs. (3.13)-(3.15). Synthetic same-basis
 * acquisitions exercise X and P separately; no optical measurement is implied.
 * N=12 is illustrative, and never changes the public/default lab preset. */
#include "nullifier.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define DELAY 12
#define MAX_MODES 128
#define WINDOW 8
static unsigned comparisons;
static const double coefficients[2] = {0.5, 2.0};

static void require(bool okay, const char *message) {
    if (!okay) { fprintf(stderr, "%s\n", message); exit(1); }
}

static void close_value(double actual, double expected) {
    require(actual == expected || (isnan(actual) && isnan(expected)) ||
            fabs(actual - expected) <= 2e-10 * fmax(1, fabs(expected)),
            "2D formula, quadrature, variance or dB differs from independent oracle");
    comparisons++;
}

static void configure(nf_processor *p, bool phase, bool ensemble) {
    nf_config c; nf_defaults(&c);
    c.capture_samples = 16384; c.history_modes = 500;
    c.guard_seconds = 0; c.end_guard = 0; c.b_delay = 0;
    strcpy(c.shape, "gaussian_linear");
    strcpy(c.basis, phase ? "p" : "x");
    strcpy(c.estimator, ensemble ? "ensemble" : "rolling");
    c.variance_modes = WINDOW; c.require_full_variance = true;
    c.row_count = 1;
    nf_row *r = &c.rows[0];
    strcpy(r->name, phase ? "larsen_2d_p" : "larsen_2d_x");
    r->start = 0; r->term_count = 8;
    r->terms[0] = (nf_term){0, 0, 1};
    r->terms[1] = (nf_term){1, 0, 1};
    r->terms[2] = (nf_term){0, 1, phase ? 1 : -1};
    r->terms[3] = (nf_term){1, 1, phase ? 1 : -1};
    r->terms[4] = (nf_term){0, DELAY, -1};
    r->terms[5] = (nf_term){1, DELAY, 1};
    r->terms[6] = (nf_term){0, DELAY + 1, phase ? 1 : -1};
    r->terms[7] = (nf_term){1, DELAY + 1, phase ? -1 : 1};
    char error[NF_ERROR_SIZE];
    require(!nf_configure(p, &c, error), error);
    /* Known independent rail calibration factors, rather than claiming that
     * synthetic data is a measured vacuum calibration. */
    p->calibrated = true;
    memcpy(p->coefficients, coefficients, sizeof(coefficients));
}

static double nullifier(const double a[MAX_MODES], const double b[MAX_MODES],
                        size_t k, bool phase) {
    if (phase)
        return a[k] + b[k] + a[k + 1] + b[k + 1] - a[k + DELAY] +
               b[k + DELAY] + a[k + DELAY + 1] - b[k + DELAY + 1];
    return a[k] + b[k] - a[k + 1] - b[k + 1] - a[k + DELAY] +
           b[k + DELAY] - a[k + DELAY + 1] + b[k + DELAY + 1];
}

static double population(const double *values, size_t count) {
    long double sum = 0, m2 = 0;
    for (size_t i = 0; i < count; ++i) sum += values[i];
    long double mean = sum / count;
    for (size_t i = 0; i < count; ++i) {
        long double delta = values[i] - mean;
        m2 += delta * delta;
    }
    return (double)(m2 / count);
}

static void check_rolling(bool phase) {
    nf_processor *p = calloc(1, sizeof(*p)); require(p != NULL, "processor allocation");
    configure(p, phase, false);
    const size_t lengths[] = {MAX_MODES, 79, DELAY};
    size_t previous_size = 0;
    for (unsigned capture = 0; capture < sizeof(lengths) / sizeof(*lengths); ++capture) {
        size_t modes = lengths[capture];
        double *q[2] = {malloc(modes * sizeof(double)), malloc(modes * sizeof(double))};
        double expected[2][MAX_MODES], values[MAX_MODES];
        require(q[0] && q[1], "quadrature allocation");
        for (size_t k = 0; k < modes; ++k) {
            q[0][k] = 0.3 * k + (capture + 1) * sin(k * 0.37);
            q[1][k] = -0.2 * k + (capture + 1) * cos(k * 0.29);
            for (unsigned ch = 0; ch < 2; ++ch)
                expected[ch][k] = q[ch][k] * coefficients[ch];
        }
        char error[NF_ERROR_SIZE];
        require(!nf_process_modes(p, q, modes, error), error);
        require(p->quadrature_count == modes, "quadrature support changed");
        for (unsigned ch = 0; ch < 2; ++ch) for (size_t k = 0; k < modes; ++k)
            close_value(p->quadratures[ch][k], expected[ch][k]);
        size_t size = modes > DELAY + 1 ? modes - DELAY - 1 : 0;
        require(p->rows[0].size == size, "2D long-delay support crosses capture boundary");
        for (size_t k = 0; k < size; ++k) {
            values[k] = nullifier(expected[0], expected[1], k, phase);
            close_value(p->rows[0].values[k], values[k]);
        }
        for (size_t k = 0; k < size; ++k) {
            size_t begin = k > WINDOW / 2 ? k - WINDOW / 2 : 0;
            size_t end = k + WINDOW / 2 < size ? k + WINDOW / 2 : size;
            double v = end - begin == WINDOW ? population(values + begin, WINDOW) : NAN;
            close_value(p->rows[0].variance[k], v);
            close_value(p->rows[0].db[k], 10 * log10(v / 4.0));
            require(p->rows[0].counts[k] == (uint64_t)isfinite(v), "rolling accepted count changed");
        }
        for (size_t k = size; k < previous_size; ++k)
            require(isnan(p->rows[0].values[k]), "short capture retained stale 2D nullifiers");
        previous_size = size;
    }
    nf_destroy(p); free(p);
}

static void check_vacuum_reference(bool phase) {
    nf_processor *p = calloc(1, sizeof(*p)); require(p != NULL, "processor allocation");
    configure(p, phase, true);
    const unsigned offsets[] = {0, 1, DELAY, DELAY + 1};
    /* Exhaust all 2^8 independent signs. Each of the eight quadratures has
     * population variance 1/2, so either signed nullifier has variance 4 and
     * 0 dB relative to its own eight-term vacuum reference. */
    for (unsigned shot = 0; shot < 256; ++shot) {
        double *q[2] = {calloc(16, sizeof(double)), calloc(16, sizeof(double))};
        require(q[0] && q[1], "vacuum quadrature allocation");
        for (unsigned offset = 0; offset < 4; ++offset)
            for (unsigned ch = 0; ch < 2; ++ch) {
                double sign = shot & (1u << (2 * offset + ch)) ? 1 : -1;
                q[ch][offsets[offset]] = sign * sqrt(0.5) / coefficients[ch];
            }
        char error[NF_ERROR_SIZE];
        require(!nf_process_modes(p, q, 16, error), error);
    }
    require(p->rows[0].size == 3 && p->rows[0].counts[0] == 256,
            "vacuum ensemble support/count changed");
    close_value(p->rows[0].variance[0], 4);
    close_value(p->rows[0].db[0], 0);
    nf_destroy(p); free(p);
}

int main(void) {
    for (unsigned phase = 0; phase < 2; ++phase) {
        check_rolling(phase != 0);
        check_vacuum_reference(phase != 0);
    }
    printf("PASS: separate Larsen 2D X/P formulas, N=12 support, per-rail normalization, vacuum reference 4; %u comparisons\n",
           comparisons);
    return 0;
}
