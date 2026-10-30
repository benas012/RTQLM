#define _POSIX_C_SOURCE 200809L
#include "nullifier.h"
#include "stream.h"
#include "vendor/cJSON.h"

#include <float.h>
#include <math.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define MAX_LINE (1024 * 1024)
volatile sig_atomic_t nf_stop_requested = 0;
static bool replay = false;
static double monotonic_ms(void) {
    struct timespec now; clock_gettime(CLOCK_MONOTONIC, &now);
    return (double)now.tv_sec * 1000 + (double)now.tv_nsec / 1e6;
}
pthread_mutex_t nf_processor_mutex = PTHREAD_MUTEX_INITIALIZER;

static void stop_requested(int signal_number) { (void)signal_number; nf_stop_requested = 1; }
static int fail(char *error, const char *message) {
    snprintf(error, NF_ERROR_SIZE, "%s", message); return -1;
}

static int keys(const cJSON *object, const char *allowed, char *error) {
    if (!cJSON_IsObject(object)) return fail(error, "expected an object");
    const cJSON *item;
    cJSON_ArrayForEach(item, object) {
        char marker[128];
        if (strlen(item->string) > 100) return fail(error, "object key is too long");
        snprintf(marker, sizeof(marker), "|%s|", item->string);
        if (strchr(item->string, '|') || !strstr(allowed, marker))
            return fail(error, "unknown command or configuration field");
        for (const cJSON *other = item->next; other; other = other->next)
            if (!strcmp(item->string, other->string)) return fail(error, "duplicate JSON key");
    }
    return 0;
}

static int number(const cJSON *item, double low, double high, double *out, char *error) {
    if (!cJSON_IsNumber(item) || !isfinite(item->valuedouble) ||
        item->valuedouble < low || item->valuedouble > high) return fail(error, "invalid numeric value");
    *out = item->valuedouble; return 0;
}

static int integer(const cJSON *item, unsigned low, unsigned high, unsigned *out, char *error) {
    double value;
    if (number(item, low, high, &value, error)) return -1;
    if (floor(value) != value) return fail(error, "expected an integer");
    *out = (unsigned)value; return 0;
}

static int string(const cJSON *item, char *out, size_t size, char *error) {
    if (!cJSON_IsString(item) || !item->valuestring[0] || strlen(item->valuestring) >= size)
        return fail(error, "invalid or overlong string");
    strcpy(out, item->valuestring); return 0;
}

static int formulas(const cJSON *array, nf_config *config, char *error) {
    if (!cJSON_IsArray(array) || cJSON_GetArraySize(array) < 1 || cJSON_GetArraySize(array) > NF_MAX_ROWS)
        return fail(error, "nullifiers requires 1 to 16 rows");
    memset(config->rows, 0, sizeof(config->rows)); config->row_count = 0;
    const cJSON *item;
    cJSON_ArrayForEach(item, array) {
        if (keys(item, "|name||start||terms|", error)) return -1;
        nf_row *row = &config->rows[config->row_count++];
        if (string(cJSON_GetObjectItemCaseSensitive(item, "name"), row->name, sizeof(row->name), error)) return -1;
        const cJSON *start = cJSON_GetObjectItemCaseSensitive(item, "start");
        if (start && integer(start, 0, NF_MAX_HISTORY - 1, &row->start, error)) return -1;
        const cJSON *terms = cJSON_GetObjectItemCaseSensitive(item, "terms");
        if (!cJSON_IsArray(terms) || cJSON_GetArraySize(terms) < 1 || cJSON_GetArraySize(terms) > NF_MAX_TERMS)
            return fail(error, "each row requires 1 to 32 terms");
        const cJSON *term;
        cJSON_ArrayForEach(term, terms) {
            if (keys(term, "|source||offset||weight|", error)) return -1;
            nf_term *t = &row->terms[row->term_count++]; t->weight = 1;
            const cJSON *source = cJSON_GetObjectItemCaseSensitive(term, "source");
            if (!cJSON_IsString(source) || (strcmp(source->valuestring, "a") && strcmp(source->valuestring, "b")))
                return fail(error, "term source must be a or b");
            t->source = !strcmp(source->valuestring, "b");
            const cJSON *offset = cJSON_GetObjectItemCaseSensitive(term, "offset");
            const cJSON *weight = cJSON_GetObjectItemCaseSensitive(term, "weight");
            if (offset && integer(offset, 0, NF_MAX_HISTORY - 1, &t->offset, error)) return -1;
            if (weight && number(weight, -1e100, 1e100, &t->weight, error)) return -1;
        }
    }
    return 0;
}

static int parse_config(const cJSON *object, nf_config *c, char *error) {
    const char *allowed = "|decimation||captureSamples||historyModes||modeSamples||minSamplesPerMode|"
        "|modeOversample||varianceWindowModes||collectionEndGuardSamples||channelBDelaySamples|"
        "|modeWidthSeconds||measurementFrequencyHz||varianceWindowSeconds||collectionGuardSeconds|"
        "|triggerTimeoutSeconds||triggerLevelVolts||gateAbsoluteThreshold||modeShape||varianceEstimator|"
        "|calibrationEstimator||triggerSource||measurementBasis||explicitModeTemplate||nullifiers|"
        "|windowMode||lockSeconds||collectSeconds||requireFullVarianceWindow|";
    if (keys(object, allowed, error) || !cJSON_GetArraySize(object))
        return fail(error, "configure requires a nonempty object with supported settings");
    const cJSON *item;
    cJSON_ArrayForEach(item, object) {
        const char *key = item->string;
#define UINT_SETTING(name, member, limit) if (!strcmp(key, name)) { \
    if (integer(item, 0, limit, &c->member, error)) { return -1; } continue; }
#define DOUBLE_SETTING(name, member, low, high) if (!strcmp(key, name)) { \
    if (number(item, low, high, &c->member, error)) { return -1; } continue; }
#define STRING_SETTING(name, member) if (!strcmp(key, name)) { \
    if (string(item, c->member, sizeof(c->member), error)) { return -1; } continue; }
        UINT_SETTING("decimation", decimation, 65536)
        UINT_SETTING("captureSamples", capture_samples, 4194304)
        UINT_SETTING("historyModes", history_modes, NF_MAX_HISTORY)
        if (!strcmp(key, "modeSamples") && cJSON_IsNull(item)) { c->mode_samples = 0; continue; }
        UINT_SETTING("modeSamples", mode_samples, NF_MAX_TEMPLATE)
        UINT_SETTING("minSamplesPerMode", min_samples, NF_MAX_TEMPLATE)
        UINT_SETTING("modeOversample", oversample, 1000)
        if (!strcmp(key, "varianceWindowModes") && cJSON_IsNull(item)) { c->variance_modes = 0; continue; }
        UINT_SETTING("varianceWindowModes", variance_modes, NF_MAX_HISTORY)
        UINT_SETTING("collectionEndGuardSamples", end_guard, 4194304)
        DOUBLE_SETTING("modeWidthSeconds", mode_width, 1e-12, 10)
        DOUBLE_SETTING("measurementFrequencyHz", frequency, 1e-9, 1e9)
        DOUBLE_SETTING("varianceWindowSeconds", variance_seconds, 1e-12, 1e6)
        DOUBLE_SETTING("collectionGuardSeconds", guard_seconds, 0, 60)
        DOUBLE_SETTING("triggerTimeoutSeconds", timeout_seconds, 0.001, 60)
        DOUBLE_SETTING("triggerLevelVolts", trigger_level, -1, 1)
        if (!strcmp(key, "gateAbsoluteThreshold") && cJSON_IsNull(item)) { c->reject_threshold = 0; continue; }
        DOUBLE_SETTING("gateAbsoluteThreshold", reject_threshold, 0, 1000)
        STRING_SETTING("modeShape", shape)
        STRING_SETTING("varianceEstimator", estimator)
        STRING_SETTING("calibrationEstimator", calibration_estimator)
        STRING_SETTING("triggerSource", trigger)
        STRING_SETTING("measurementBasis", basis)
        STRING_SETTING("windowMode", window_mode)
        DOUBLE_SETTING("lockSeconds", lock_seconds, 0, 60)
        DOUBLE_SETTING("collectSeconds", collect_seconds, 1e-9, 60)
        if (!strcmp(key, "requireFullVarianceWindow")) {
            if (!cJSON_IsBool(item)) return fail(error, "requireFullVarianceWindow must be a boolean");
            c->require_full_variance = cJSON_IsTrue(item); continue;
        }
        if (!strcmp(key, "channelBDelaySamples")) {
            double value;
            if (number(item, -65536, 65536, &value, error)) return -1;
            if (floor(value) != value) return fail(error, "delay must be an integer");
            c->b_delay = (int)value;
        } else if (!strcmp(key, "explicitModeTemplate")) {
            if (!cJSON_IsArray(item) || cJSON_GetArraySize(item) > NF_MAX_TEMPLATE)
                return fail(error, "explicit template must be an array of at most 4096 coefficients");
            c->template_count = 0;
            const cJSON *coefficient;
            cJSON_ArrayForEach(coefficient, item)
                if (number(coefficient, -1e100, 1e100, &c->explicit_template[c->template_count++], error)) return -1;
        } else if (!strcmp(key, "nullifiers") && formulas(item, c, error)) return -1;
#undef UINT_SETTING
#undef DOUBLE_SETTING
#undef STRING_SETTING
    }
    if (c->template_count && c->mode_samples && c->template_count != c->mode_samples)
        return fail(error, "explicit template length must match modeSamples");
    return 0;
}

static bool is_lab_formula(const nf_config *config) {
    if (config->row_count != 1) return false;
    const nf_row *r = &config->rows[0];
    bool p = !strcmp(config->basis, "p");
    if (strcmp(r->name, p ? "cluster_p" : "cluster_x") || r->start != (unsigned)p || r->term_count != 4)
        return false;
    const unsigned sources[] = {0, 0, 1, 1}, offsets[] = {0, 1, 0, 1};
    const double weights[] = {1, p ? -1 : 1, 1, p ? 1 : -1};
    for (unsigned i = 0; i < 4; ++i)
        if (r->terms[i].source != sources[i] || r->terms[i].offset != offsets[i] ||
            r->terms[i].weight != weights[i]) return false;
    return true;
}

static cJSON *stepped_numbers(const double *values, size_t count, size_t stride) {
    cJSON *array = cJSON_CreateArray();
    for (size_t i = 0; i < count; i += stride) {
        cJSON *item;
        if (!isfinite(values[i])) item = cJSON_CreateNull();
        else if (stride > 1) {
            // A display snapshot is already sampled. Format each selected
            // double once; cJSON's generic path formats and parses it again.
            char text[32]; snprintf(text, sizeof(text), "%.17g", values[i]);
            item = cJSON_CreateRaw(text);
        } else item = cJSON_CreateNumber(values[i]);
        cJSON_AddItemToArray(array, item);
    }
    return array;
}
static cJSON *numbers(const double *values, size_t count) { return stepped_numbers(values, count, 1); }

static cJSON *status_reply(const nf_processor *p) {
    cJSON *reply = cJSON_CreateObject();
    cJSON_AddBoolToObject(reply, "ok", true);
    cJSON_AddStringToObject(reply, "backend", replay ? "c-replay" : "redpitaya-arm-c");
    cJSON_AddNumberToObject(reply, "revision", (double)p->revision);
    cJSON_AddBoolToObject(reply, "enabled", p->enabled);
    cJSON_AddBoolToObject(reply, "calibrated", p->calibrated);
    cJSON_AddBoolToObject(reply, "calibrating", p->calibrating);
    cJSON_AddBoolToObject(reply, "hasResult", p->has_result);
    cJSON_AddNumberToObject(reply, "captures", (double)p->captures);
    cJSON_AddNumberToObject(reply, "sampleRateHz", p->sample_rate);
    cJSON_AddNumberToObject(reply, "modeSamples", p->mode_size);
    cJSON_AddNumberToObject(reply, "modePeriodSeconds", p->sample_rate ? p->mode_size / p->sample_rate : 0);
    cJSON_AddBoolToObject(reply, "prefixTruncated", p->truncated);
    cJSON_AddItemToObject(reply, "stream", nf_stream_status());
    cJSON *counts = cJSON_AddObjectToObject(reply, "calibrationModeCounts");
    cJSON_AddNumberToObject(counts, "a", (double)p->calibration[0].count);
    cJSON_AddNumberToObject(counts, "b", (double)p->calibration[1].count);
    cJSON *coefficients = cJSON_AddObjectToObject(reply, "shotNoiseCoefficients");
    if (p->calibrated) {
        cJSON_AddNumberToObject(coefficients, "a", p->coefficients[0]);
        cJSON_AddNumberToObject(coefficients, "b", p->coefficients[1]);
    }
    const nf_config *c = &p->config;
    cJSON *config = cJSON_AddObjectToObject(reply, "config");
#define READ_NUMBER(key, member) cJSON_AddNumberToObject(config, key, c->member)
#define READ_STRING(key, member) cJSON_AddStringToObject(config, key, c->member)
    READ_NUMBER("decimation", decimation); READ_NUMBER("captureSamples", capture_samples);
    READ_NUMBER("historyModes", history_modes); READ_NUMBER("minSamplesPerMode", min_samples);
    READ_NUMBER("modeOversample", oversample); READ_NUMBER("channelBDelaySamples", b_delay);
    READ_NUMBER("modeWidthSeconds", mode_width); READ_NUMBER("measurementFrequencyHz", frequency);
    READ_NUMBER("varianceWindowSeconds", variance_seconds); READ_NUMBER("collectionGuardSeconds", guard_seconds);
    READ_NUMBER("collectionEndGuardSamples", end_guard); READ_NUMBER("triggerTimeoutSeconds", timeout_seconds);
    READ_NUMBER("triggerLevelVolts", trigger_level);
    READ_STRING("modeShape", shape); READ_STRING("varianceEstimator", estimator);
    READ_STRING("calibrationEstimator", calibration_estimator); READ_STRING("triggerSource", trigger);
    READ_STRING("measurementBasis", basis);
    READ_STRING("windowMode", window_mode); READ_NUMBER("lockSeconds", lock_seconds);
    READ_NUMBER("collectSeconds", collect_seconds);
    if (c->mode_samples) READ_NUMBER("modeSamples", mode_samples);
    else cJSON_AddNullToObject(config, "modeSamples");
    if (c->variance_modes) READ_NUMBER("varianceWindowModes", variance_modes);
    else cJSON_AddNullToObject(config, "varianceWindowModes");
    if (c->reject_threshold) READ_NUMBER("gateAbsoluteThreshold", reject_threshold);
    else cJSON_AddNullToObject(config, "gateAbsoluteThreshold");
    cJSON_AddItemToObject(config, "explicitModeTemplate", numbers(c->explicit_template, c->template_count));
    cJSON *rows = cJSON_AddArrayToObject(config, "nullifiers");
    for (unsigned i = 0; i < c->row_count; ++i) {
        const nf_row *row = &c->rows[i]; cJSON *object = cJSON_CreateObject();
        cJSON_AddItemToArray(rows, object); cJSON_AddStringToObject(object, "name", row->name);
        cJSON_AddNumberToObject(object, "start", row->start);
        cJSON *terms = cJSON_AddArrayToObject(object, "terms");
        for (unsigned j = 0; j < row->term_count; ++j) {
            const nf_term *term = &row->terms[j]; cJSON *item = cJSON_CreateObject();
            cJSON_AddItemToArray(terms, item);
            cJSON_AddStringToObject(item, "source", term->source ? "b" : "a");
            cJSON_AddNumberToObject(item, "offset", term->offset);
            cJSON_AddNumberToObject(item, "weight", term->weight);
        }
    }
#undef READ_NUMBER
#undef READ_STRING
    return reply;
}

static cJSON *latest_reply(const nf_processor *p, unsigned max_points) {
    cJSON *reply = status_reply(p);
    size_t longest = p->quadrature_count;
    for (unsigned i = 0; i < p->config.row_count; ++i)
        if (p->rows[i].size > longest) longest = p->rows[i].size;
    size_t stride = max_points && longest > max_points ? (longest + max_points - 1) / max_points : 1;
    cJSON_AddNumberToObject(reply, "displayStride", (double)stride);
    cJSON *values = cJSON_AddObjectToObject(reply, "nullifiers");
    cJSON *variances = cJSON_AddObjectToObject(reply, "variances");
    cJSON *db = cJSON_AddObjectToObject(reply, "squeezingDb");
    cJSON *counts = cJSON_AddObjectToObject(reply, "varianceCounts");
    cJSON *starts = cJSON_AddObjectToObject(reply, "indexStarts");
    cJSON *quadratures = cJSON_AddObjectToObject(reply, "quadratures");
    cJSON_AddItemToObject(quadratures, "a", stepped_numbers(p->quadratures[0], p->quadrature_count, stride));
    cJSON_AddItemToObject(quadratures, "b", stepped_numbers(p->quadratures[1], p->quadrature_count, stride));
    for (unsigned i = 0; i < p->config.row_count; ++i) {
        const nf_row_state *r = &p->rows[i]; const char *name = p->config.rows[i].name;
        cJSON_AddItemToObject(values, name, stepped_numbers(r->values, r->size, stride));
        cJSON_AddItemToObject(variances, name, stepped_numbers(r->variance, r->size, stride));
        if (p->calibrated) cJSON_AddItemToObject(db, name, stepped_numbers(r->db, r->size, stride));
        cJSON *array = cJSON_AddArrayToObject(counts, name);
        for (size_t j = 0; j < r->size; j += stride) cJSON_AddItemToArray(array, cJSON_CreateNumber((double)r->counts[j]));
        cJSON_AddNumberToObject(starts, name, p->config.rows[i].start);
    }
    return reply;
}

static nf_processor *copy_result(const nf_processor *p) {
    nf_processor *copy = malloc(sizeof(*copy));
    if (!copy) return NULL;
    *copy = *p;
    memset(copy->rows, 0, sizeof(copy->rows));
    copy->quadratures[0] = copy->quadratures[1] = NULL;
    for (unsigned channel = 0; channel < 2; ++channel) if (p->quadrature_count) {
        copy->quadratures[channel] = malloc(p->quadrature_count * sizeof(double));
        if (!copy->quadratures[channel]) goto failed;
        memcpy(copy->quadratures[channel], p->quadratures[channel], p->quadrature_count * sizeof(double));
    }
    for (unsigned i = 0; i < p->config.row_count; ++i) {
        const nf_row_state *source = &p->rows[i]; nf_row_state *dest = &copy->rows[i];
        dest->size = source->size;
        if (!source->size) continue;
        size_t bytes = source->size * sizeof(double);
        dest->values = malloc(bytes); dest->variance = malloc(bytes); dest->db = malloc(bytes);
        dest->counts = malloc(source->size * sizeof(uint64_t));
        if (!dest->values || !dest->variance || !dest->db || !dest->counts) goto failed;
        memcpy(dest->values, source->values, bytes); memcpy(dest->variance, source->variance, bytes);
        memcpy(dest->db, source->db, bytes);
        memcpy(dest->counts, source->counts, source->size * sizeof(uint64_t));
    }
    return copy;
failed:
    nf_destroy(copy); free(copy); return NULL;
}

static int replay_samples(const cJSON *samples, float **a, float **b, size_t *count, char *error) {
    if (keys(samples, "|a||b|", error)) return -1;
    const cJSON *channels[2] = {cJSON_GetObjectItemCaseSensitive(samples, "a"),
                              cJSON_GetObjectItemCaseSensitive(samples, "b")};
    int n = cJSON_GetArraySize(channels[0]);
    if (!cJSON_IsArray(channels[0]) || !cJSON_IsArray(channels[1]) || n < 1 ||
        n > 4194304 || n != cJSON_GetArraySize(channels[1])) return fail(error, "sample arrays must be nonempty and aligned");
    *count = (size_t)n; *a = calloc(*count, sizeof(float)); *b = calloc(*count, sizeof(float));
    if (!*a || !*b) return fail(error, "cannot allocate replay samples");
    float *output[2] = {*a, *b};
    for (unsigned channel = 0; channel < 2; ++channel) {
        size_t index = 0; const cJSON *item;
        cJSON_ArrayForEach(item, channels[channel]) {
            double value;
            if (number(item, -FLT_MAX, FLT_MAX, &value, error)) return -1;
            output[channel][index++] = (float)value;
        }
    }
    return 0;
}

static cJSON *execute(nf_processor *p, const cJSON *request, char *error) {
    const cJSON *cmd = cJSON_GetObjectItemCaseSensitive(request, "command");
    if (!cJSON_IsString(cmd)) { fail(error, "command must be a string"); return NULL; }
    const char *command = cmd->valuestring;
    const char *allowed = !strcmp(command, "configure") ? "|command||config|" :
        !strcmp(command, "finish_calibration") ? "|command||minimumModes|" :
        !strcmp(command, "start_stream") ? "|command||port||channelMask|" :
        !strcmp(command, "latest") ? "|command||maxPoints|" :
        !strcmp(command, "output") ? "|command||channel||enabled||waveform||frequency||amplitude||offset||phase|" :
        !strcmp(command, "capture") ? (replay ? "|command||samples||previewPoints||maxPoints|" :
                                      "|command||previewPoints||maxPoints|") : "|command|";
    if (keys(request, allowed, error)) return NULL;
    if (!strcmp(command, "configure")) {
        nf_config *candidate = malloc(sizeof(*candidate));
        if (!candidate) { fail(error, "cannot allocate configuration"); return NULL; }
        *candidate = p->config;
        const cJSON *config_object = cJSON_GetObjectItemCaseSensitive(request, "config");
        int result = parse_config(config_object, candidate, error);
        if (!result && cJSON_GetObjectItemCaseSensitive(config_object, "measurementBasis") &&
            !cJSON_GetObjectItemCaseSensitive(config_object, "nullifiers") && is_lab_formula(&p->config)) {
            bool basis_p = !strcmp(candidate->basis, "p"); nf_row *row = &candidate->rows[0];
            strcpy(row->name, basis_p ? "cluster_p" : "cluster_x"); row->start = basis_p;
            row->terms[1].weight = basis_p ? -1 : 1; row->terms[3].weight = basis_p ? 1 : -1;
        }
        if (!result) result = nf_configure(p, candidate, error);
        free(candidate);
        if (result) return NULL;
        nf_stream_reset();
    } else if (!strcmp(command, "start_calibration")) {
        if (!p->enabled) { fail(error, "configure before calibration"); return NULL; }
        nf_start_calibration(p);
    } else if (!strcmp(command, "finish_calibration")) {
        unsigned minimum = 100; const cJSON *item = cJSON_GetObjectItemCaseSensitive(request, "minimumModes");
        if (item && integer(item, 2, 1000000000, &minimum, error)) return NULL;
        if (nf_finish_calibration(p, minimum, error)) return NULL;
    } else if (!strcmp(command, "clear_calibration")) nf_clear_calibration(p);
    else if (!strcmp(command, "reset")) nf_clear_results(p);
    else if (!strcmp(command, "disable")) { nf_clear_calibration(p); p->enabled = false; }
    else if (!strcmp(command, "start_stream")) {
        unsigned port = 18902, mask = 3;
        const cJSON *port_value = cJSON_GetObjectItemCaseSensitive(request, "port");
        const cJSON *mask_value = cJSON_GetObjectItemCaseSensitive(request, "channelMask");
        if ((port_value && integer(port_value, 1024, 65535, &port, error)) ||
            (mask_value && integer(mask_value, 1, 3, &mask, error))) return NULL;
        if (nf_stream_start(p, port, mask, error)) return NULL;
    } else if (!strcmp(command, "stop_stream")) { /* Joined outside the processor lock. */ }
    else if (!strcmp(command, "capture")) {
        unsigned preview_points = 0, max_points = 0;
        const cJSON *preview_limit = cJSON_GetObjectItemCaseSensitive(request, "previewPoints");
        const cJSON *display_limit = cJSON_GetObjectItemCaseSensitive(request, "maxPoints");
        if ((preview_limit && integer(preview_limit, 16, 4096, &preview_points, error)) ||
            (display_limit && integer(display_limit, 16, NF_MAX_HISTORY, &max_points, error))) return NULL;
        if (nf_stream_running()) { fail(error, "stop streaming before a burst capture"); return NULL; }
        if (!p->enabled) { fail(error, "configure before capture"); return NULL; }
        float *a = NULL, *b = NULL; size_t count = p->config.capture_samples;
        int result = replay ? replay_samples(cJSON_GetObjectItemCaseSensitive(request, "samples"), &a, &b, &count, error) :
                              nf_acquire(&p->config, &a, &b, error);
        double processing_begin = monotonic_ms();
        if (!result) result = nf_process(p, a, b, count, error);
        double format_begin = monotonic_ms();
        cJSON *reply = result ? NULL : (p->calibrating ? status_reply(p) : latest_reply(p, max_points));
        if (reply && preview_points) {
            cJSON *preview = cJSON_AddObjectToObject(reply, "preview");
            cJSON *rails[2] = {cJSON_AddArrayToObject(preview, "a"), cJSON_AddArrayToObject(preview, "b")};
            if (preview_points > count) preview_points = (unsigned)count;
            for (unsigned i = 0; i < preview_points; ++i) {
                cJSON_AddItemToArray(rails[0], cJSON_CreateNumber(a[i]));
                cJSON_AddItemToArray(rails[1], cJSON_CreateNumber(b[i]));
            }
        }
        free(a); free(b);
        if (result) { nf_clear_results(p); return NULL; }
        if (reply) {
            nf_acquire_timing acquired = nf_acquire_last_timing();
            cJSON *timing = cJSON_AddObjectToObject(reply, "captureTimingMs");
            cJSON_AddNumberToObject(timing, "setup", acquired.setup_ms);
            cJSON_AddNumberToObject(timing, "sampleWait", acquired.wait_ms);
            cJSON_AddNumberToObject(timing, "read", acquired.read_ms);
            cJSON_AddNumberToObject(timing, "directChannels", acquired.direct_channels);
            cJSON_AddNumberToObject(timing, "processing", format_begin - processing_begin);
            cJSON_AddNumberToObject(timing, "jsonBuild", monotonic_ms() - format_begin);
        }
        return reply;
    } else if (!strcmp(command, "latest")) {
        unsigned max_points = 0;
        const cJSON *limit = cJSON_GetObjectItemCaseSensitive(request, "maxPoints");
        if (limit && integer(limit, 16, NF_MAX_HISTORY, &max_points, error)) return NULL;
        if (!p->has_result) { fail(error, "no processed capture available"); return NULL; }
        return latest_reply(p, max_points);
    } else if (!strcmp(command, "output")) {
        unsigned channel, waveform;
        double frequency, amplitude, offset, phase;
        const cJSON *enabled = cJSON_GetObjectItemCaseSensitive(request, "enabled");
        if (!cJSON_IsBool(enabled)) { fail(error, "enabled must be a boolean"); return NULL; }
        if (integer(cJSON_GetObjectItemCaseSensitive(request, "channel"), 1, 2, &channel, error) ||
            integer(cJSON_GetObjectItemCaseSensitive(request, "waveform"), 0, 10, &waveform, error) ||
            number(cJSON_GetObjectItemCaseSensitive(request, "frequency"), 1e-9, 50000000, &frequency, error) ||
            number(cJSON_GetObjectItemCaseSensitive(request, "amplitude"), 0, 1, &amplitude, error) ||
            number(cJSON_GetObjectItemCaseSensitive(request, "offset"), -1, 1, &offset, error) ||
            number(cJSON_GetObjectItemCaseSensitive(request, "phase"), -360, 360, &phase, error)) return NULL;
        if (amplitude + fabs(offset) > 1) { fail(error, "output peak exceeds 1 V"); return NULL; }
        if (replay) { fail(error, "output requires the board SDK"); return NULL; }
        if (nf_set_output(channel, cJSON_IsTrue(enabled), waveform, frequency,
                          amplitude, offset, phase, error)) return NULL;
    } else if (strcmp(command, "status")) { fail(error, "unknown command"); return NULL; }
    return status_reply(p);
}

int main(int argc, char **argv) {
    if (argc == 2 && !strcmp(argv[1], "--replay")) replay = true;
    else if (argc != 1) { fprintf(stderr, "usage: %s [--replay]\n", argv[0]); return 2; }
    struct sigaction action; memset(&action, 0, sizeof(action)); action.sa_handler = stop_requested;
    sigaction(SIGINT, &action, NULL); sigaction(SIGTERM, &action, NULL);
    sigaction(SIGHUP, &action, NULL);
    nf_processor *processor = calloc(1, sizeof(*processor)); char *line = malloc(MAX_LINE + 1);
    if (!processor || !line) { free(processor); free(line); return 2; }
    nf_defaults(&processor->config); nf_clear_calibration(processor);
    while (!nf_stop_requested) {
        size_t n = 0; bool oversized = false, nul = false; int ch;
        while ((ch = fgetc(stdin)) != EOF && ch != '\n') {
            if (ch == 0) nul = true;
            if (n < MAX_LINE) line[n++] = (char)ch; else oversized = true;
        }
        if (ch == EOF && (!n || nf_stop_requested)) break;
        line[n] = 0; char error[NF_ERROR_SIZE] = {0};
        cJSON *request = NULL, *reply = NULL;
        if (oversized || nul) fail(error, "invalid or oversized command line");
        else {
            request = cJSON_ParseWithLengthOpts(line, n + 1, NULL, true);
            if (request) {
                const cJSON *command = cJSON_GetObjectItemCaseSensitive(request, "command");
                if (cJSON_IsObject(request) && cJSON_GetArraySize(request) == 1 &&
                    cJSON_IsString(command) && !strcmp(command->valuestring, "stop_stream")) nf_stream_stop();
                bool snapshot = cJSON_IsString(command) && !strcmp(command->valuestring, "latest");
                nf_processor *copy = NULL;
                pthread_mutex_lock(&nf_processor_mutex);
                if (snapshot) copy = copy_result(processor);
                else reply = execute(processor, request, error);
                pthread_mutex_unlock(&nf_processor_mutex);
                if (snapshot) {
                    // JSON formatting and slow command readers must not hold
                    // the calculation worker's lock for a display snapshot.
                    if (copy) { reply = execute(copy, request, error); nf_destroy(copy); free(copy); }
                    else fail(error, "cannot allocate result snapshot");
                }
            }
            else fail(error, "invalid JSON command");
        }
        if (!reply) {
            reply = cJSON_CreateObject(); cJSON_AddBoolToObject(reply, "ok", false);
            cJSON_AddStringToObject(reply, "error", error[0] ? error : "command failed");
        }
        char *text = cJSON_PrintUnformatted(reply);
        if (!text) { cJSON_Delete(request); cJSON_Delete(reply); break; }
        puts(text); fflush(stdout); free(text); cJSON_Delete(request); cJSON_Delete(reply);
        if (ch == EOF) break;
    }
    nf_stream_stop();
    char shutdown_error[NF_ERROR_SIZE] = {0};
    int shutdown_result = nf_acquire_shutdown(shutdown_error);
    if (shutdown_result) fprintf(stderr, "%s\n", shutdown_error);
    nf_destroy(processor); free(processor); free(line); return shutdown_result ? 1 : 0;
}
