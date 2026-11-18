/* Independent, two-pass long-double oracle for the public mode/statistics API. */
#include "nullifier.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MODES 400
#define SHOTS 4
static unsigned comparisons;

static void require(bool okay, const char *message) {
    if (!okay) { fprintf(stderr, "%s\n", message); exit(1); }
}

static void close_value(double actual, double expected, const char *kind) {
    if (!(actual == expected || (isnan(actual) && isnan(expected)) ||
          fabs(actual - expected) <= 3e-8 + 3e-9 * fabs(expected))) {
        fprintf(stderr, "%s: %.17g != %.17g (comparison %u)\n", kind, actual, expected, comparisons);
        exit(1);
    }
    comparisons++;
}

static double population(const double *values, size_t count, uint64_t *accepted) {
    long double sum = 0, m2 = 0;
    *accepted = 0;
    for (size_t i = 0; i < count; ++i) if (isfinite(values[i])) {
        sum += values[i]; (*accepted)++;
    }
    if (*accepted < 2) return NAN;
    long double mean = sum / *accepted;
    for (size_t i = 0; i < count; ++i) if (isfinite(values[i])) {
        long double delta = values[i] - mean;
        m2 += delta * delta;
    }
    return (double)(m2 / *accepted);
}

static double expected_db(double variance, double reference) {
    return 10 * log10(variance / reference);
}

static double variance_error(double expected) {
    return 1e-10 + 3e-9 * fabs(expected);
}

static void close_variance(double actual, double expected) {
    require(actual == expected || (isnan(actual) && isnan(expected)) ||
            fabs(actual - expected) <= variance_error(expected),
            "population variance differs from independent two-pass oracle");
    comparisons++;
}

static void close_db(double actual, double expected, double lower, double upper) {
    /* A near-zero variance magnifies absolute recursive roundoff in log10.
     * Propagate the separately checked variance error instead of allowing a
     * fixed dB discrepancy. The saved baseline has the same sensitivity: at
     * DC 10000, its largest variance error is 7.82e-11 in these scenarios. */
    if (!(actual == expected || (isnan(actual) && isnan(expected)) ||
          (actual >= lower - 3e-8 && actual <= upper + 3e-8))) {
        fprintf(stderr, "squeezing dB: %.17g != %.17g, bounds [%.17g, %.17g]\n",
                actual, expected, lower, upper);
        exit(1);
    }
    comparisons++;
}

static void configure(nf_processor *p, const char *estimator, unsigned window, bool full) {
    nf_config c; nf_defaults(&c);
    c.capture_samples = 16384; c.history_modes = 500;
    c.guard_seconds = 0; c.end_guard = 0; c.b_delay = 0;
    c.variance_modes = window; c.require_full_variance = full;
    strcpy(c.estimator, estimator);
    c.row_count = 2;
    strcpy(c.rows[0].name, "mixed"); c.rows[0].start = 1; c.rows[0].term_count = 5;
    c.rows[0].terms[0] = (nf_term){0, 0, 1};
    c.rows[0].terms[1] = (nf_term){1, 1, -1};
    c.rows[0].terms[2] = (nf_term){0, 2, 0.5};
    c.rows[0].terms[3] = (nf_term){0, 0, -0.25};
    c.rows[0].terms[4] = (nf_term){1, 3, 0};
    strcpy(c.rows[1].name, "single"); c.rows[1].start = 0; c.rows[1].term_count = 2;
    c.rows[1].terms[0] = (nf_term){0, 0, 1};
    c.rows[1].terms[1] = (nf_term){1, 0, 0};
    char error[NF_ERROR_SIZE];
    require(!nf_configure(p, &c, error), error);
    p->calibrated = true;
    p->coefficients[0] = 0.75; p->coefficients[1] = 1.25;
}

static void check_estimator(const char *estimator, unsigned window, bool full, bool gaps) {
    nf_processor *p = calloc(1, sizeof(*p)); require(p != NULL, "processor allocation");
    configure(p, estimator, window, full);
    bool lab = !strcmp(estimator, "lab_rolling"), ensemble = !strcmp(estimator, "ensemble");
    double observations[2][MODES][SHOTS] = {{{0}}};
    double variance_sum[2][MODES] = {{0}}, db_sum[2][MODES] = {{0}};
    double db_lower_sum[2][MODES] = {{0}}, db_upper_sum[2][MODES] = {{0}};
    uint64_t counts[2][MODES] = {{0}};
    size_t retained[2] = {0};
    const size_t lengths[SHOTS] = {400, 320, 400, 251};
    const double references[2] = {0.90625, 0.5};
    for (unsigned shot = 0; shot < SHOTS; ++shot) {
        size_t modes = lengths[shot];
        double *q[2] = {malloc(modes * sizeof(double)), malloc(modes * sizeof(double))};
        double source[2][MODES], values[2][MODES];
        require(q[0] && q[1], "quadrature allocation");
        for (size_t i = 0; i < modes; ++i) {
            q[0][i] = 10000 + (shot + 1) * sin(i * 0.37);
            q[1][i] = 10000 + (shot + 1) * cos(i * 0.29);
            if (gaps && i % 71 == 0) q[0][i] = NAN;
            if (gaps && i % 83 == 0) q[1][i] = INFINITY;
            source[0][i] = q[0][i] * 0.75; source[1][i] = q[1][i] * 1.25;
        }
        char error[NF_ERROR_SIZE];
        require(!nf_process_modes(p, q, modes, error), error);
        nf_process_timing timing = nf_process_last_timing(p);
        require(timing.normalization_ms >= 0 && timing.formulas_ms >= 0 && timing.rolling_ms >= 0 &&
                timing.statistics_ms >= 0 && timing.total_ms >= timing.normalization_ms +
                timing.formulas_ms + timing.rolling_ms + timing.statistics_ms,
                "statistics stage timing does not partition processing");
        for (unsigned row = 0; row < 2; ++row) {
            size_t size = modes - (row ? 0 : 4);
            for (size_t i = 0; i < size; ++i) {
                values[row][i] = row ? 0.0 + source[0][i] :
                    ((0.0 + source[0][i + 1]) - source[1][i + 2]) +
                    0.5 * source[0][i + 3] - 0.25 * source[0][i + 1];
                close_value(p->rows[row].values[i], values[row][i], "formula");
            }
            for (size_t i = 0; i < size; ++i) {
                double v;
                if (ensemble) {
                    observations[row][i][shot] = values[row][i];
                    // Missing indices in a shorter shot never contribute to ensemble statistics.
                    double admitted[SHOTS]; size_t n = 0;
                    for (unsigned s = 0; s <= shot; ++s)
                        if (i < lengths[s] - (row ? 0 : 4)) admitted[n++] = observations[row][i][s];
                    v = population(admitted, n, &counts[row][i]);
                } else {
                    size_t left = window / 2, right = lab ? left : window - left;
                    size_t begin = i > left ? i - left : 0;
                    size_t end = i + right < size ? i + right : size;
                    uint64_t accepted;
                    v = population(values[row] + begin, end - begin, &accepted);
                    if (full && accepted != window) v = NAN;
                    if (lab && !isnan(v)) {
                        counts[row][i]++; variance_sum[row][i] += v;
                        db_sum[row][i] += expected_db(v, references[row]);
                        db_lower_sum[row][i] += expected_db(fmax(0, v - variance_error(v)), references[row]);
                        db_upper_sum[row][i] += expected_db(v + variance_error(v), references[row]);
                    } else if (!lab) counts[row][i] = isfinite(v) ? 1 : 0;
                }
                double db = expected_db(v, references[row]);
                double lower = isnan(v) ? NAN : expected_db(fmax(0, v - variance_error(v)), references[row]);
                double upper = isnan(v) ? NAN : expected_db(v + variance_error(v), references[row]);
                if (lab) {
                    v = counts[row][i] ? variance_sum[row][i] / counts[row][i] : NAN;
                    db = counts[row][i] ? db_sum[row][i] / counts[row][i] : NAN;
                    lower = counts[row][i] ? db_lower_sum[row][i] / counts[row][i] : NAN;
                    upper = counts[row][i] ? db_upper_sum[row][i] / counts[row][i] : NAN;
                }
                require(p->rows[row].counts[i] == counts[row][i], "variance accepted-count mismatch");
                close_variance(p->rows[row].variance[i], v);
                close_db(p->rows[row].db[i], db, lower, upper);
                if (!lab) close_value(p->rows[row].db[i],
                    expected_db(p->rows[row].variance[i], references[row]), "dB conversion");
            }
            size_t expected_size = (lab || ensemble) && retained[row] > size ? retained[row] : size;
            require(p->rows[row].size == expected_size, "retained row support mismatch");
            for (size_t i = size; i < expected_size; ++i)
                require(isnan(p->rows[row].values[i]), "short capture retained stale nullifier values");
            retained[row] = expected_size;
        }
        if (lab && full && window % 2)
            require(timing.log10_calls == 0, "odd lab full-window mask performed a logarithm");
    }
    nf_destroy(p); free(p);
}

static void check_logarithms(void) {
    nf_processor *p = calloc(1, sizeof(*p)); require(p != NULL, "processor allocation");
    configure(p, "rolling", 6, true);
    for (unsigned calibrated = 0; calibrated < 2; ++calibrated) {
        p->calibrated = calibrated;
        double *q[2] = {calloc(MODES, sizeof(double)), calloc(MODES, sizeof(double))};
        char error[NF_ERROR_SIZE];
        require(q[0] && q[1], "zero quadrature allocation");
        require(!nf_process_modes(p, q, MODES, error), error);
        require(nf_process_last_timing(p).log10_calls == 0, "zero or NaN variance called log10");
        require(calibrated ? p->rows[1].db[50] == -INFINITY : isnan(p->rows[1].db[50]),
                "zero variance dB changed");
        require(isnan(p->rows[1].db[0]), "full-window edge must remain NaN");
    }
    nf_destroy(p); free(p);
}

int main(void) {
    const char *estimators[] = {"rolling", "lab_rolling", "ensemble"};
    const unsigned windows[] = {2, 5, 6, 401};
    for (unsigned estimator = 0; estimator < 3; ++estimator)
    for (unsigned window = 0; window < 4; ++window)
    for (unsigned full = 0; full < 2; ++full)
    for (unsigned gaps = 0; gaps < 2; ++gaps)
        check_estimator(estimators[estimator], windows[window], full, gaps);
    check_logarithms();
    printf("PASS: 48 estimator/window/mask/gap scenarios, %u independent comparisons; logarithms and stage timing\n",
           comparisons);
    return 0;
}
