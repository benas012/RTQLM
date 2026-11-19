/* Independent voltage-array oracle for direct raw ADC integration. No hardware. */
#include "nullifier.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define COUNT 4096
static int16_t words[2][COUNT];
static float signal[2][COUNT], volts[2][16384];
static const double scale[2] = {1.0 / 8192, 1.0 / 4096};
static const double zero[2] = {1.25, -3.5};
static unsigned checks, scenario;

static void require(bool okay, const char *message) {
    if (!okay) { fprintf(stderr, "%s\n", message); exit(1); }
}
static void close_value(double actual, double expected) {
    if (!(actual == expected || (isnan(actual) && isnan(expected)) ||
          fabs(actual - expected) <= 2e-10 * fmax(1.0, fabs(expected)))) {
        fprintf(stderr, "scenario %u comparison %u: raw %.17g, voltage %.17g\n",
                scenario, checks, actual, expected);
        require(false, "raw/voltage numeric mismatch");
    }
    checks++;
}

static void close_db(double actual, double expected) {
    // Nearly equal ensemble observations amplify roundoff in log(variance).
    require(actual == expected || (isnan(actual) && isnan(expected)) ||
            fabs(actual - expected) < 1e-5, "raw/voltage dB mismatch");
    checks++;
}

static void inputs(nf_raw_channel raw[2], unsigned shift, bool measured) {
    for (unsigned ch = 0; ch < 2; ++ch) {
        unsigned start = (shift + ch * 2) % COUNT;
        raw[ch] = (nf_raw_channel){{words[ch] + start, words[ch]}, {COUNT - start, start}, zero[ch], volts[ch]};
        for (unsigned code = 0; code < 16384; ++code) {
            int value = code < 8192 ? (int)code : (int)code - 16384;
            volts[ch][code] = (float)((value - zero[ch]) * scale[ch]);
        }
        for (unsigned i = 0; i < COUNT; ++i) {
            int value = (int)((i * (ch ? 179u : 131u) + i * i * 17u + shift * 13u) % 601) - 300;
            if (measured) value /= 2;
            if (measured && i == 81) value = ch ? 8191 : -8192;
            if (measured && i == 82) value = ch ? -1 : 0;
            uint16_t code = (uint16_t)value & 16383;
            // Ignore storage bits above the 14-bit ADC payload.
            words[ch][(start + i) % COUNT] = (int16_t)(code | ((i & 3) << 14));
            signal[ch][i] = (float)((value - zero[ch]) * scale[ch]);
        }
    }
}

static void project(nf_processor *raw, nf_processor *reference, unsigned shift, bool measured) {
    char error[NF_ERROR_SIZE]; nf_raw_channel input[2]; inputs(input, shift, measured);
    double *q[2]; size_t modes;
    require(!nf_project_raw(raw, input, COUNT, q, &modes, error), error);
    require(!nf_process_modes(raw, q, modes, error), error);
    require(!nf_process(reference, signal[0], signal[1], COUNT, error), error);
    require(raw->raw_counts && !reference->raw_counts, "input units not reported");
    if (raw->calibrating) {
        require(raw->calibration[0].count == reference->calibration[0].count,
                "first raw capture cancelled calibration or changed accepted modes");
        return;
    }
    require(raw->quadrature_count == reference->quadrature_count, "different mode count");
    for (unsigned ch = 0; ch < 2; ++ch) for (size_t k = 0; k < modes; ++k) {
        double expected = reference->quadratures[ch][k];
        close_value(raw->quadratures[ch][k], raw->calibrated ? expected : expected / scale[ch]);
    }
    if (raw->calibrated) for (unsigned row = 0; row < raw->config.row_count; ++row) {
        require(raw->rows[row].size == reference->rows[row].size, "different formula support");
        for (size_t k = 0; k < raw->rows[row].size; ++k) {
            close_value(raw->rows[row].values[k], reference->rows[row].values[k]);
            close_value(raw->rows[row].variance[k], reference->rows[row].variance[k]);
            close_db(raw->rows[row].db[k], reference->rows[row].db[k]);
        }
    }
}

static int edge_value(unsigned i, unsigned channel, unsigned pattern) {
    int value = pattern == 0 ? 8191 : pattern == 1 ? -8192 :
                (int)((i + (pattern - 2) * COUNT) % 16384) - 8192;
    return channel ? -value - 1 : value;
}

static unsigned flat_limits(void) {
    const unsigned widths[] = {2, 3, 4, 7, 8, 15, 16, 38, 39, 4096};
    const unsigned starts[] = {0, 1, 2, 3, COUNT - 1};
    unsigned scenarios = 0; char error[NF_ERROR_SIZE];
    nf_processor *p = calloc(1, sizeof(*p)); require(p != NULL, "edge allocation");
    for (unsigned width = 0; width < sizeof(widths) / sizeof(*widths); ++width) {
        nf_config c; nf_defaults(&c); c.mode_samples = widths[width]; c.min_samples = 2;
        c.capture_samples = COUNT; c.history_modes = COUNT; c.b_delay = 0;
        c.guard_seconds = 0; c.end_guard = 0; strcpy(c.shape, "identity");
        require(!nf_configure(p, &c, error), error);
        for (unsigned offset = 0; offset < sizeof(starts) / sizeof(*starts); ++offset)
        for (unsigned pattern = 0; pattern < 6; ++pattern) {
            nf_raw_channel raw[2];
            for (unsigned ch = 0; ch < 2; ++ch) {
                unsigned start = (starts[offset] + 2 * ch) % COUNT;
                raw[ch] = (nf_raw_channel){{words[ch] + start, words[ch]},
                                          {COUNT - start, start}, zero[ch], NULL};
                for (unsigned i = 0; i < COUNT; ++i)
                    words[ch][(start + i) % COUNT] = (int16_t)(((uint16_t)edge_value(i, ch, pattern) & 16383) |
                                                             ((i & 3) << 14));
            }
            double *q[2]; size_t modes;
            require(!nf_project_raw(p, raw, COUNT, q, &modes, error), error);
            require(modes == COUNT / c.mode_samples, "cached boundaries changed edge mode count");
            for (unsigned ch = 0; ch < 2; ++ch) for (size_t k = 0; k < modes; ++k) {
                int total = 0;
                for (unsigned j = 0; j < c.mode_samples; ++j)
                    total += edge_value((unsigned)k * c.mode_samples + j, ch, pattern);
                double expected = (total - zero[ch] * c.mode_samples) /
                                  sqrt(p->sample_rate * c.mode_samples);
                close_value(q[ch][k], expected);
            }
            free(q[0]); free(q[1]); scenarios++;
        }
    }
    nf_destroy(p); free(p); return scenarios;
}

int main(void) {
    unsigned scenarios = 0;
    const char *shapes[] = {"identity", "gaussian_linear", "lab_gaussian_linear_cosine", "identity"};
    const char *estimators[] = {"rolling", "lab_rolling", "ensemble"};
    for (unsigned shape = 0; shape < 4; ++shape) for (int delay = -3; delay <= 3; delay += 3)
    for (unsigned fixed = 0; fixed < 2; ++fixed) for (unsigned gate = 0; gate < 2; ++gate)
    for (unsigned estimator = 0; estimator < 3; ++estimator) {
        char error[NF_ERROR_SIZE]; nf_config config; nf_defaults(&config);
        scenario = scenarios;
        config.capture_samples = COUNT; config.history_modes = 2000; config.min_samples = 2;
        config.mode_samples = fixed ? 4 : 0; config.guard_seconds = 8e-9; config.end_guard = 3;
        config.b_delay = delay; config.reject_threshold = gate ? 0.2 : 0;
        config.variance_modes = 5; strcpy(config.shape, shapes[shape]);
        if (shape == 3) {
            config.template_count = fixed ? 4 : 8;
            for (unsigned i = 0; i < config.template_count; ++i) config.explicit_template[i] = i % 2 ? -2 : 1;
        }
        strcpy(config.estimator, estimators[estimator]);
        strcpy(config.calibration_estimator, estimator == 1 ? "segment_mean" : "pooled");
        nf_processor *raw = calloc(1, sizeof(*raw)), *reference = calloc(1, sizeof(*reference));
        require(raw && reference, "allocation");
        require(!nf_configure(raw, &config, error) && !nf_configure(reference, &config, error), error);
        unsigned shift = (scenarios % 2) ? COUNT - 37 : 3;
        project(raw, reference, shift, false);
        nf_start_calibration(raw); nf_start_calibration(reference);
        project(raw, reference, shift, false);
        project(raw, reference, COUNT - 39, false);
        require(!nf_finish_calibration(raw, 100, error) && !nf_finish_calibration(reference, 100, error), error);
        project(raw, reference, shift, true);
        project(raw, reference, COUNT - 39, true);
        // Switching representation must invalidate a reference in different units.
        require(!nf_process(raw, signal[0], signal[1], COUNT, error), error);
        require(!raw->calibrated && !raw->raw_counts, "reference survived input-unit change");
        nf_destroy(raw); nf_destroy(reference); free(raw); free(reference); scenarios++;
    }
    unsigned edges = flat_limits();
    printf("PASS: %u scenarios, %u flat edge cases, %u comparisons; offsets, wrap, gates, shapes, delays and normalization\n",
           scenarios, edges, checks);
    return 0;
}
