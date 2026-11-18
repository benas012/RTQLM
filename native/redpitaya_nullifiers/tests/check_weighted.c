/* Independent per-sample oracle for weighted raw projection, including ADC
 * sign extension, ignored storage bits, DMA wrap and the common rejection gate.
 * The oracle uses the configured double weights: it tests optimized arithmetic,
 * not the physical choice of temporal mode (covered by timing/physics tests).
 */
#include "nullifier.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <float.h>

#define COUNT 8192
static int16_t words[2][COUNT];
static float volts[2][16384];
static unsigned comparisons, scenarios;

static void require(bool okay, const char *message) {
    if (!okay) { fprintf(stderr, "scenario %u: %s\n", scenarios, message); exit(1); }
}

static int value(unsigned i, unsigned ch, unsigned pattern) {
    if (pattern == 0) return ch ? -8192 : 8191;
    if (pattern == 1) return i % 2 ? -8192 : 8191;
    return (int)((i + (pattern - 2) * COUNT + ch * 71) % 16384) - 8192;
}

static void inputs(nf_raw_channel raw[2], unsigned shift, unsigned pattern, bool gate) {
    for (unsigned ch = 0; ch < 2; ++ch) {
        unsigned start = (shift + ch * 2) % COUNT;
        double zero = ch ? -3.5 : 1.25;
        for (unsigned i = 0; i < COUNT; ++i) {
            unsigned code = (unsigned)value(i, ch, pattern) & 16383;
            words[ch][(start + i) % COUNT] = (int16_t)(code | ((i & 3) << 14));
        }
        for (unsigned code = 0; code < 16384; ++code) {
            int sample = code < 8192 ? (int)code : (int)code - 16384;
            volts[ch][code] = (float)((sample - zero) / 8192.0);
        }
        raw[ch] = (nf_raw_channel){{words[ch] + start, words[ch]}, {COUNT - start, start},
                                  zero, gate ? volts[ch] : NULL};
    }
}

static void compare_projection(nf_processor *p, unsigned shift, unsigned pattern, bool gate, bool dma) {
    nf_raw_channel raw[2]; inputs(raw, shift, pattern, gate);
    char error[NF_ERROR_SIZE] = {0}; double *q[2]; size_t modes;
    int result = dma ? nf_project_raw_dma(p, raw, COUNT, q, &modes, error) :
                       nf_project_raw(p, raw, COUNT, q, &modes, error);
    require(!result, error);
    size_t begin = (size_t)nearbyint(p->config.guard_seconds * p->sample_rate);
    size_t delay = (size_t)abs(p->config.b_delay);
    size_t expected_count = nf_mode_count(p, COUNT - begin - p->config.end_guard - delay, 0);
    require(modes == expected_count, "weighted mode count changed");
    for (size_t k = 0; k < modes; ++k) {
        size_t base = (size_t)nf_mode_boundary(p, k);
        unsigned width = (unsigned)(nf_mode_boundary(p, k + 1) - base);
        const double *weights = width == p->mode_size ? p->mode : p->mode_large;
        long double sum[2] = {0, 0}, absolute[2] = {0, 0}; bool valid = true;
        for (unsigned ch = 0; ch < 2; ++ch) {
            size_t pos = begin + base + ((ch == 0 ? p->config.b_delay < 0 : p->config.b_delay > 0) ? delay : 0);
            for (unsigned j = 0; j < width; ++j) {
                int sample = value((unsigned)pos + j, ch, pattern);
                long double product = ((long double)sample - raw[ch].zero) * weights[j];
                sum[ch] += product; absolute[ch] += fabsl(product);
                if (gate && fabs(volts[ch][(unsigned)sample & 16383]) >= p->config.reject_threshold)
                    valid = false;
            }
        }
        for (unsigned ch = 0; ch < 2; ++ch) {
            double expected = valid ? (double)(sum[ch] / p->sample_rate) : NAN;
            // Forward-error bound scales with the absolute dot product, not its
            // possibly near-zero result. This catches cancellation regressions
            // without claiming relative accuracy when the true sum vanishes.
            double tolerance = (double)(8 * DBL_EPSILON * (width + 1) * absolute[ch] / p->sample_rate);
            if (!((isnan(expected) && isnan(q[ch][k])) ||
                  fabs(q[ch][k] - expected) <= fmax(1e-18, tolerance))) {
                fprintf(stderr, "width %u channel %u mode %zu raw %.17g oracle %.17g\n",
                        width, ch, k, q[ch][k], expected);
                require(false, "weighted scalar oracle mismatch");
            }
            comparisons++;
        }
    }
    free(q[0]); free(q[1]); scenarios++;
}

int main(void) {
    const unsigned widths[] = {2, 3, 4, 7, 8, 15, 16, 38, 39, 40, 127, 128, 511, 512, 4095, 4096, 0};
    const unsigned starts[] = {0, 1, 2, 3, COUNT - 1, COUNT - 37, COUNT - 39};
    const char *shapes[] = {"gaussian_linear", "lab_gaussian_linear_cosine", "explicit"};
    nf_processor *p = calloc(1, sizeof(*p));
    require(p != NULL, "processor allocation failed");
    for (unsigned shape = 0; shape < 3; ++shape)
    for (unsigned w = 0; w < sizeof(widths) / sizeof(*widths); ++w)
    for (unsigned gate = 0; gate < 2; ++gate) {
        if (shape == 2 && !widths[w]) continue;
        nf_config c; nf_defaults(&c);
        c.capture_samples = COUNT; c.history_modes = COUNT; c.mode_samples = widths[w];
        c.min_samples = 2; c.oversample = 8; c.guard_seconds = 0; c.end_guard = 0; c.b_delay = 0;
        c.reject_threshold = gate ? 0.75 : 0;
        if (shape < 2) strcpy(c.shape, shapes[shape]);
        else {
            c.template_count = widths[w];
            for (unsigned i = 0; i < c.template_count; ++i)
                c.explicit_template[i] = ((int)(i % 13) - 6) * (1 + (i % 7) / 11.0);
            /* Two samples must not both be zero. */
            c.explicit_template[0] = 0.125;
        }
        char error[NF_ERROR_SIZE] = {0};
        require(!nf_configure(p, &c, error), error);
        for (unsigned offset = 0; offset < sizeof(starts) / sizeof(*starts); ++offset)
        for (unsigned pattern = 0; pattern < 4; ++pattern)
        for (unsigned dma = 0; dma < 2; ++dma)
            compare_projection(p, starts[offset], pattern, gate != 0, dma != 0);
    }
    nf_destroy(p); free(p);
    printf("PASS: %u weighted CPU/DMA scenarios, %u comparisons; scalar double oracle, ADC extremes, all codes, wrap and gates\n",
           scenarios, comparisons);
    return 0;
}
