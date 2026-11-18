/* Experimental binary command worker. Control and results retain the C daemon
 * JSON schema. Binary payloads are planar little-endian ADC words followed by
 * optional exact SDK voltage lookup tables. One request is in flight at a time.
 * Run over authenticated SSH or a local pipe, never an exposed command socket. */
#define NF_BUFFERED_WORKER
#include "commands.c"
#include <sys/socket.h>
#include <sys/time.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <unistd.h>
#include <signal.h>
#include <errno.h>
#include <poll.h>

/* Explicit opt-in LAN transport: one client, bearer token supplied over SSH,
 * bounded handshake, then close listener. Payload is unencrypted on this LAN. */
static int accept_tcp(const char *port_text) {
    char *end = NULL; long port = strtol(port_text, &end, 10);
    const char *token = getenv("NF_BUFFER_TOKEN");
    if (!end || *end || port < 1024 || port > 65535 || !token || strlen(token) != 64) return -1;
    int listener = socket(AF_INET, SOCK_STREAM, 0), yes = 1;
    if (listener < 0) return -1;
    setsockopt(listener, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof(yes));
    struct sockaddr_in address = {0}; address.sin_family = AF_INET;
    address.sin_port = htons((uint16_t)port); address.sin_addr.s_addr = htonl(INADDR_ANY);
    if (bind(listener, (struct sockaddr *)&address, sizeof(address)) || listen(listener, 1)) {
        fprintf(stderr, "ERROR: TCP port %ld: %s\n", port, strerror(errno)); fflush(stderr);
        close(listener); return -1;
    }
    fprintf(stderr, "READY\n"); fflush(stderr);
    /* An SSH client can disappear between launch and TCP connect. Do not leave
     * its listener owning the port indefinitely across later app restarts. */
    struct pollfd waiting = {.fd = listener, .events = POLLIN};
    int ready = poll(&waiting, 1, 15000);
    if (ready <= 0 || !(waiting.revents & POLLIN)) {
        fprintf(stderr, "ERROR: TCP client did not connect within 15 seconds\n"); fflush(stderr);
        close(listener); return -1;
    }
    int fd = accept(listener, NULL, NULL); close(listener);
    if (fd < 0) return -1;
    setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &yes, sizeof(yes));
    int send_buffer = 1024 * 1024;
    setsockopt(fd, SOL_SOCKET, SO_SNDBUF, &send_buffer, sizeof(send_buffer));
    struct timeval timeout = {15, 0};
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
    char received[65]; size_t at = 0;
    while (at < sizeof(received)) {
        ssize_t n = recv(fd, received+at, sizeof(received)-at, 0);
        if (n <= 0) { close(fd); return -1; } at += (size_t)n;
    }
    if (memcmp(received, token, 64) || received[64] != '\n') { close(fd); return -1; }
    /* The deadline protects authentication, not a legitimate client's pause.
     * Keep the configured/calibrated processor alive across Stop or an idle UI;
     * TCP keepalive still releases it after a lost network connection. */
    timeout.tv_sec = 0;
    int keep_idle = 15, keep_interval = 5, keep_count = 3;
    if (setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout)) ||
        setsockopt(fd, SOL_SOCKET, SO_KEEPALIVE, &yes, sizeof(yes)) ||
        setsockopt(fd, IPPROTO_TCP, TCP_KEEPIDLE, &keep_idle, sizeof(keep_idle)) ||
        setsockopt(fd, IPPROTO_TCP, TCP_KEEPINTVL, &keep_interval, sizeof(keep_interval)) ||
        setsockopt(fd, IPPROTO_TCP, TCP_KEEPCNT, &keep_count, sizeof(keep_count))) {
        fprintf(stderr, "ERROR: TCP session keepalive: %s\n", strerror(errno));
        close(fd); return -1;
    }
    if (dup2(fd, STDIN_FILENO) < 0 || dup2(fd, STDOUT_FILENO) < 0) { close(fd); return -1; }
    close(fd); return 0;
}

static int write_bytes(const void *data, size_t bytes) {
    return fwrite(data, 1, bytes, stdout) == bytes ? 0 : -1;
}

int main(int argc, char **argv) {
    struct sigaction action; memset(&action, 0, sizeof(action)); action.sa_handler = stop_requested;
    sigaction(SIGINT, &action, NULL); sigaction(SIGTERM, &action, NULL);
    sigaction(SIGHUP, &action, NULL);
    signal(SIGPIPE, SIG_IGN);
    if (argc != 1 && (argc != 3 || strcmp(argv[1], "--listen") || accept_tcp(argv[2]))) return 2;
    const uint16_t endian = 1;
    if (*(const unsigned char *)&endian != 1) return 2;
    nf_processor *p = calloc(1, sizeof(*p));
    char *line = malloc(MAX_LINE + 2);
    if (!p || !line) return 2;
    nf_defaults(&p->config);
    char *serialized = NULL; size_t capacity = 0;
    uint64_t capture_sequence = 0;
    while (!nf_stop_requested && fgets(line, MAX_LINE + 2, stdin)) {
        char error[NF_ERROR_SIZE] = {0};
        if (!strchr(line, '\n')) break;
        cJSON *request = cJSON_Parse(line), *reply = NULL;
        const cJSON *cmd = cJSON_GetObjectItemCaseSensitive(request, "command");
        bool capture = cJSON_IsString(cmd) && !strcmp(cmd->valuestring, "capture_binary");
        bool process = cJSON_IsString(cmd) && !strcmp(cmd->valuestring, "process_binary");
        if (capture || process) {
            unsigned count = 0;
            unsigned measurement_count = 0;
            double zero[2] = {0, 0};
            nf_raw_channel raw[2] = {0};
            int16_t *owned = NULL; float *tables = NULL;
            float *preview_a = NULL, *preview_b = NULL;
            unsigned preview_points = 0;
            unsigned max_points = 1024;
            bool has_tables = p->config.reject_threshold > 0;
            int result = !p->enabled ? fail(error, "configure before binary capture") : 0;
            if (!result && integer(cJSON_GetObjectItemCaseSensitive(request, "count"),
                                   1, p->config.capture_samples, &count, error)) result = -1;
            if (!result && capture && count != p->config.capture_samples)
                result = fail(error, "capture_binary count must equal captureSamples");
            measurement_count = count;
            const cJSON *preview = cJSON_GetObjectItemCaseSensitive(request, "previewPoints");
            if (!result && preview && integer(preview, 16, 4096, &preview_points, error)) result = -1;
            if (preview_points > count) preview_points = count;
            const cJSON *display = cJSON_GetObjectItemCaseSensitive(request, "maxPoints");
            if (!result && display && integer(display, 16, NF_MAX_HISTORY, &max_points, error)) result = -1;
            const cJSON *measurement = cJSON_GetObjectItemCaseSensitive(request, "measurementCount");
            if (!result && measurement && integer(measurement, 1, count, &measurement_count, error)) result = -1;
            double started = monotonic_ms();
            if (!result && capture) result = nf_acquire_raw_preview(p, raw, preview_points, &preview_a, &preview_b, error);
            if (!result && process) {
                const cJSON *offsets = cJSON_GetObjectItemCaseSensitive(request, "zero");
                if (!cJSON_IsArray(offsets) || cJSON_GetArraySize(offsets) != 2) result = fail(error, "two ADC offsets required");
                for (unsigned ch = 0; !result && ch < 2; ++ch)
                    result = number(cJSON_GetArrayItem(offsets, ch), -DBL_MAX, DBL_MAX, &zero[ch], error);
                if (!result) {
                    owned = malloc((size_t)count * 4);
                    if (has_tables) tables = malloc(2 * 16384 * sizeof(float));
                    if (!owned || (has_tables && !tables)) result = fail(error, "allocation failed");
                    else if (fread(owned, 4, count, stdin) != count ||
                             (has_tables && fread(tables, sizeof(float), 32768, stdin) != 32768))
                        result = fail(error, "truncated binary capture");
                    for (unsigned ch = 0; !result && ch < 2; ++ch) {
                        raw[ch].data[0] = owned + ch * count; raw[ch].size[0] = measurement_count;
                        raw[ch].zero = zero[ch]; raw[ch].volts = tables ? tables + ch * 16384 : NULL;
                    }
                }
            }
            double acquired = monotonic_ms();
            if (!result && capture) {
                nf_acquire_timing t = nf_acquire_last_timing();
                printf("{\"ok\":true,\"count\":%u,\"zero\":[%.17g,%.17g],\"tables\":%s,"
                       "\"acquireMs\":%.9g,\"setupMs\":%.9g,\"waitMs\":%.9g,\"copyMs\":%.9g,"
                       "\"startIntervalMs\":%.9g", count, raw[0].zero, raw[1].zero,
                       has_tables ? "true" : "false", acquired-started, t.setup_ms, t.wait_ms,
                       t.raw_copy_ms, t.start_interval_ms);
                printf(",\"sequence\":%llu,\"captureBeginMonotonicMs\":%.9f,\"captureEndMonotonicMs\":%.9f",
                       (unsigned long long)capture_sequence++, started, acquired);
                printf(",\"sampleDurationMs\":%.9g,\"gateChecks\":%llu,\"gateMeanIntervalMs\":%.9g,"
                       "\"gateMaxIntervalMs\":%.9g,\"gateIntervalCount\":%llu,\"gateIntervalHistogram\":[",
                       t.sample_duration_ms, (unsigned long long)t.gate_checks,
                       t.gate_intervals ? t.gate_interval_sum_ms / t.gate_intervals : 0,
                       t.gate_max_interval_ms, (unsigned long long)t.gate_intervals);
                for (unsigned i=0;i<32;++i)
                    printf("%s%llu", i ? "," : "", (unsigned long long)t.gate_interval_histogram[i]);
                printf("]");
                if (preview_points) {
                    printf(",\"preview\":{\"a\":[");
                    for (unsigned i=0;i<preview_points;++i) printf("%s%.9g", i ? "," : "", preview_a[i]);
                    printf("],\"b\":[");
                    for (unsigned i=0;i<preview_points;++i) printf("%s%.9g", i ? "," : "", preview_b[i]);
                    printf("]}");
                }
                printf("}\n");
                for (unsigned ch = 0; ch < 2; ++ch)
                    if (write_bytes(raw[ch].data[0], count * 2)) return 3;
                if (has_tables) for (unsigned ch = 0; ch < 2; ++ch)
                    if (!raw[ch].volts || write_bytes(raw[ch].volts, 16384 * sizeof(float))) return 3;
                if (fflush(stdout)) return 3;
            } else if (!result) {
                double *q[2] = {NULL, NULL}; size_t modes = 0;
                result = nf_project_raw(p, raw, measurement_count, q, &modes, error);
                double projected = monotonic_ms();
                if (!result) result = nf_process_modes(p, q, modes, error);
                if (!result) {
                    reply = p->calibrating ? status_reply(p) : result_reply(p, max_points, false);
                    cJSON_AddNumberToObject(reply, "projectionMs", projected-acquired);
                    cJSON_AddNumberToObject(reply, "processingMs", monotonic_ms()-projected);
                    cJSON_AddNumberToObject(reply, "processingKernelMs", nf_process_last_timing(p).total_ms);
                }
            }
            free(owned); free(tables); free(preview_a); free(preview_b);
            if (capture && !result) { cJSON_Delete(request); continue; }
            /* A failed binary request makes framing ambiguous: close after error. */
            if (result) nf_stop_requested = 1;
        } else if (request) reply = execute(p, request, error);
        if (!reply) {
            reply = cJSON_CreateObject(); cJSON_AddBoolToObject(reply, "ok", false);
            cJSON_AddStringToObject(reply, "error", error[0] ? error : "invalid request");
        }
        char *text = serialize_reply(reply, &serialized, &capacity);
        if (!text) break;
        puts(text); fflush(stdout);
        cJSON_Delete(request); cJSON_Delete(reply);
        if (nf_stop_requested) break;
    }
    char error[NF_ERROR_SIZE] = {0}; nf_acquire_shutdown(error);
    nf_destroy(p); free(p); free(line); free(serialized);
    return 0;
}
