/* Full 20 ms records with 5 ms HIGH/LOW: independent sample and variance oracles. */
#include "nullifier.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>

#define SAMPLES 2500000u
#define STORED 2500032u
#define RATE 125000000.0
#define WIDTH 40u

static void require(bool ok, const char *message) {
    if (!ok) { fprintf(stderr, "FAIL: %s\n", message); exit(1); }
}
static uint16_t word(const nf_raw_channel *raw, size_t i) {
    return (uint16_t)(i < raw->size[0] ? raw->data[0][i] : raw->data[1][i - raw->size[0]]);
}
static bool high(unsigned pattern, size_t i) {
    if (pattern == 1) return false;
    if (pattern == 2) return true;
    if (pattern == 3) return i == 100001u;
    if (pattern == 4) return i != 100001u;
    return ((i / 625000u) % 2) == 0;
}
static void fill(int16_t *data, size_t split, unsigned channel, unsigned pattern) {
    for (size_t i = 0; i < STORED; ++i) {
        bool physical_high = high(pattern, i);
        int value = (int)(i / WIDTH % 7) + (i >= 1250000u ? 1000 : 0) + (int)channel;
        if (physical_high) value = 8191;
        data[(split + i) % STORED] = (int16_t)((value & 0x3fff) | 0x8000 | (physical_high ? 0x4000 : 0));
    }
}
static double projected(const nf_raw_channel *raw, size_t start, bool calibrating) {
    double sum = 0;
    for (size_t i = start; i < start + WIDTH; ++i) {
        uint16_t w = word(raw, i);
        if (!calibrating && (w & 0x4000)) return NAN;
        sum += (int)(w & 8191) - (int)(w & 8192);
    }
    return sum / sqrt(RATE * WIDTH);
}
static void check(unsigned pattern, int delay, size_t tile_words, bool calibrate, bool history) {
    nf_processor *p = calloc(1, sizeof(*p));
    nf_config c; nf_defaults(&c);
    c.capture_samples = STORED; c.measurement_samples = SAMPLES;
    c.history_modes = 70000; c.mode_samples = WIDTH;
    c.guard_seconds = 0; c.end_guard = 0; c.b_delay = delay;
    c.variance_modes = 4; c.min_samples = 2;
    snprintf(c.shape, sizeof(c.shape), "identity");
    c.collect_seconds = .020;
    c.row_count = 3;
    c.rows[0] = (nf_row){.name="rail", .term_count=1, .terms={{0, 0, 1}}};
    c.rows[1] = (nf_row){.name="across_high", .term_count=2, .terms={{0, 0, 1}, {0, 31250, 1}}};
    c.rows[2] = (nf_row){.name="neighbours", .term_count=2, .terms={{0, 0, 1}, {0, 1, 1}}};
    if (calibrate) c.rows[2].terms[1].source = 1;
    char error[NF_ERROR_SIZE]; require(p != NULL, "processor allocation");
    require(!nf_configure(p, &c, error), error);
    if (calibrate) nf_start_calibration(p);
    int16_t *data[2] = {malloc(STORED * sizeof(int16_t)), malloc(STORED * sizeof(int16_t))};
    require(data[0] && data[1], "record allocation");
    size_t splits[2] = {37, STORED / 2 + 7};
    nf_raw_channel raw[2];
    for (unsigned ch = 0; ch < 2; ++ch)
        raw[ch] = (nf_raw_channel){{data[ch] + splits[ch], data[ch]},
                                   {STORED - splits[ch], splits[ch]}, 0, NULL};
    int16_t *tile = tile_words ? malloc(tile_words * sizeof(int16_t)) : NULL;
    for (unsigned shot = 0; shot < (history ? 2u : 1u); ++shot) {
        unsigned gate = history && !shot ? 1 : pattern;
        for (unsigned ch = 0; ch < 2; ++ch) {
            fill(data[ch], splits[ch], ch, calibrate ? 1 : gate);
            if (calibrate)
                for (size_t i = 0; i < STORED; ++i)
                    if (high(gate, i)) data[ch][(splits[ch] + i) % STORED] |= (int16_t)0x4000;
        }
        require(!nf_validate_tagged_pair(raw, error), error);
        nf_tagged_plan plan; double *q[2];
        require(!nf_prepare_tagged(p, SAMPLES, &plan, q, error), error);
        for (unsigned ch = 0; ch < 2; ++ch)
            require(nf_project_tagged_rail(p, &raw[ch], plan.start[ch], plan.modes,
                                          tile, tile_words, q[ch]) & 0x8000, "valid format rejected");
        nf_finish_tagged(p, &plan, q, true);
        size_t accepted = 0;
        long double sum[2] = {0}, squares[2] = {0};
        for (size_t k = 0; k < plan.modes; ++k) {
            double expected[2] = {projected(&raw[0], plan.start[0] + k * WIDTH, calibrate),
                                  projected(&raw[1], plan.start[1] + k * WIDTH, calibrate)};
            bool ok = isfinite(expected[0]) && isfinite(expected[1]);
            accepted += ok;
            for (unsigned ch = 0; ch < 2; ++ch) {
                if (ok) {
                    require(fabs(q[ch][k] - expected[ch]) < 1e-12, "valid projection changed");
                    sum[ch] += expected[ch]; squares[ch] += (long double)expected[ch] * expected[ch];
                }
                else require(isnan(q[ch][k]), "HIGH/transition mode integrated or indices compressed");
            }
        }
        require(p->included_modes == accepted && p->excluded_modes == plan.modes - accepted,
                "gate mode counts differ from raw E1 oracle");
        require(plan.modes == (SAMPLES - (size_t)abs(delay)) / WIDTH, "record time compressed");
        require(!nf_process_modes(p, q, plan.modes, error), error);
        if (calibrate) {
            double coefficients[2];
            for (unsigned ch = 0; ch < 2; ++ch) {
                require(p->calibration[ch].count == plan.modes, "E1 excluded shot-noise modes");
                require(p->segment_variances[ch].count == 1, "E1 split reference capture");
                long double mean = sum[ch] / plan.modes;
                double v = (double)(squares[ch] / plan.modes - mean * mean);
                require(fabs(p->calibration[ch].m2 / plan.modes - v) < 1e-12,
                        "shot-noise variance differs from full-record oracle");
                coefficients[ch] = 1 / sqrt(2 * v);
            }
            require(!nf_finish_calibration(p, 100, error), error);
            for (unsigned ch = 0; ch < 2; ++ch)
                require(fabs(p->coefficients[ch] - coefficients[ch]) < 1e-8,
                        "shot-noise coefficient excludes LOW data");
            continue;
        }
        const double *values = p->rows[0].values;
        size_t first = 0;
        while (first < plan.modes) {
            if (!isfinite(values[first])) {
                require(isnan(p->rows[0].variance[first]), "historical result filled current HIGH gap");
                ++first; continue;
            }
            size_t end = first;
            while (end < plan.modes && isfinite(values[end])) ++end;
            for (size_t i = first; i < end; ++i) {
                size_t begin = i > first + 2 ? i - 2 : first;
                size_t stop = i + 2 < end ? i + 2 : end;
                double mean = 0, expected = 0;
                for (size_t j = begin; j < stop; ++j) mean += values[j];
                mean /= (double)(stop - begin);
                for (size_t j = begin; j < stop; ++j) expected += (values[j] - mean) * (values[j] - mean);
                expected /= (double)(stop - begin);
                if (!history || !shot)
                    require(fabs(p->rows[0].variance[i] - expected) < 1e-12,
                            "rolling variance crossed HIGH or mixed separate LOW intervals");
            }
            first = end;
        }
        if (gate == 0)
            for (size_t i = 0; i < p->rows[1].size; ++i)
                require(isnan(p->rows[1].values[i]), "formula offsets jumped across HIGH interval");
    }
    /* Marker faults and disagreement must still reject; LOW alone must not. */
    data[1][(splits[1] + 4) % STORED] ^= 0x4000;
    require(nf_validate_tagged_pair(raw, error) != 0, "mismatched E1 tags accepted");
    free(tile); free(data[0]); free(data[1]); nf_destroy(p); free(p);
}
int main(void) {
    const size_t tiles[] = {0, 64, 256, 1024};
    for (unsigned i = 0; i < 4; ++i) {
        check(0, 0, tiles[i], false, false);
        check(0, 2, tiles[i], false, false);
        check(0, -2, tiles[i], false, false);
    }
    check(1, 0, 256, false, false);
    check(2, 0, 256, false, false);
    for (unsigned i = 0; i < 4; ++i) {
        check(3, 2, tiles[i], false, false);
        check(4, -2, tiles[i], false, false);
        check(0, 2, tiles[i], true, false);
        check(1, -2, tiles[i], true, false);
        check(2, 0, tiles[i], true, false);
    }
    check(0, 0, 256, false, true);
    puts("PASS: LOW admitted, HIGH/mixed rejected, wrap, B delays, scalar/tile paths, calibration and variance gaps");
}
