#define _POSIX_C_SOURCE 200809L
#include "stream.h"
#include <arpa/inet.h>
#include <errno.h>
#include <math.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

#define HEADER 128
#define MAX_BLOCK (2097152 + HEADER)
#define QUEUE_COUNT 32
#define QUEUE_BYTES (16 * 1024 * 1024)

typedef struct {
    unsigned char *channel[2];
    size_t bytes[2];
    uint64_t pack_id, epoch;
    unsigned rate;
    bool lost;
} packet;

static struct {
    nf_processor *processor;
    pthread_t reader, worker;
    pthread_mutex_t mutex;
    pthread_cond_t available;
    atomic_bool stop;
    bool running;
    int listener, upstream, downstream;
    unsigned mask, port, used;
    packet *queue[QUEUE_COUNT];
    size_t queued_bytes;
    uint64_t epoch, packets, dropped, loss_packets, results;
    char error[NF_ERROR_SIZE];
} stream = {.mutex = PTHREAD_MUTEX_INITIALIZER, .available = PTHREAD_COND_INITIALIZER,
            .listener = -1, .upstream = -1, .downstream = -1};

static uint64_t u64(const unsigned char *p) {
    uint64_t n = 0;
    for (unsigned i = 0; i < 8; ++i) n |= (uint64_t)p[i] << (i * 8);
    return n;
}
static unsigned u32(const unsigned char *p) {
    return (unsigned)p[0] | ((unsigned)p[1] << 8) | ((unsigned)p[2] << 16) | ((unsigned)p[3] << 24);
}
static void free_packet(packet *p) {
    if (p) { free(p->channel[0]); free(p->channel[1]); free(p); }
}
static size_t packet_bytes(const packet *p) { return p->bytes[0] + p->bytes[1]; }
static void clear_queue(void) {
    for (unsigned i = 0; i < stream.used; ++i) free_packet(stream.queue[i]);
    stream.used = 0; stream.queued_bytes = 0;
}
void nf_stream_reset(void) {
    pthread_mutex_lock(&stream.mutex);
    stream.epoch++; clear_queue();
    pthread_mutex_unlock(&stream.mutex);
}
static void set_error(const char *message) {
    pthread_mutex_lock(&stream.mutex);
    snprintf(stream.error, sizeof(stream.error), "%s", message);
    pthread_mutex_unlock(&stream.mutex);
}
static int transfer(int fd, unsigned char *data, size_t size, bool writing) {
    while (size && !atomic_load(&stream.stop)) {
        ssize_t count = writing ? send(fd, data, size, MSG_NOSIGNAL) : recv(fd, data, size, 0);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) return -1;
        data += count; size -= (size_t)count;
    }
    return size ? -1 : 0;
}
static int read_channel(packet *p, unsigned channel) {
    static const unsigned char magic[16] = {255,255,255,255,160,160,160,160,255,255,255,255,160,160,160,160};
    unsigned char header[HEADER];
    if (transfer(stream.upstream, header, HEADER, false)) return -1;
    uint64_t size = u64(header + 16);
    if (memcmp(header, magic, 16) || size <= HEADER || size > MAX_BLOCK ||
        header[53] != channel || (header[54] != 8 && header[54] != 16) ||
        header[52] < 8 || header[52] > 16 || !u32(header + 48) ||
        (size - HEADER) % (header[54] / 8)) return -1;
    p->channel[channel] = malloc((size_t)size);
    if (!p->channel[channel]) return -1;
    p->bytes[channel] = (size_t)size;
    memcpy(p->channel[channel], header, HEADER);
    if (transfer(stream.upstream, p->channel[channel] + HEADER, (size_t)size - HEADER, false)) return -1;
    uint64_t id = u64(header + 24);
    unsigned rate = u32(header + 48);
    if (channel == 1 && p->channel[0] && (p->pack_id != id || p->rate != rate)) return -1;
    p->pack_id = id; p->rate = rate; p->lost |= u64(header + 32) != 0;
    return 0;
}
static int local_connection(void) {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) return -1;
    struct sockaddr_in address = {.sin_family = AF_INET, .sin_port = htons(18900)};
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (connect(fd, (struct sockaddr *)&address, sizeof(address))) { close(fd); return -1; }
    int one = 1, buffer = 4 * 1024 * 1024;
    setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));
    setsockopt(fd, SOL_SOCKET, SO_RCVBUF, &buffer, sizeof(buffer));
    return fd;
}
static void *reader(void *unused) {
    (void)unused;
    while (!atomic_load(&stream.stop)) {
        struct pollfd listening = {.fd = stream.listener, .events = POLLIN};
        if (poll(&listening, 1, 100) <= 0) continue;
        int downstream = accept(stream.listener, NULL, NULL);
        if (downstream < 0) continue;
        int one = 1;
        setsockopt(downstream, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));
        pthread_mutex_lock(&stream.mutex); stream.downstream = downstream; pthread_mutex_unlock(&stream.mutex);
        int upstream = local_connection();
        pthread_mutex_lock(&stream.mutex); stream.upstream = upstream; pthread_mutex_unlock(&stream.mutex);
        nf_stream_reset();
        if (upstream >= 0) {
            for (;;) {
                packet *p = calloc(1, sizeof(*p));
                if (!p) break;
                bool failed = false;
                for (unsigned channel = 0; channel < 2; ++channel)
                    if ((stream.mask & (1u << channel)) && read_channel(p, channel)) { failed = true; break; }
                if (!failed) for (unsigned channel = 0; channel < 2; ++channel)
                    if (p->channel[channel] && transfer(downstream, p->channel[channel], p->bytes[channel], true)) failed = true;
                if (failed || atomic_load(&stream.stop)) { free_packet(p); break; }
                pthread_mutex_lock(&stream.mutex);
                stream.packets++;
                if (p->lost) stream.loss_packets++;
                if (stream.used == QUEUE_COUNT || stream.queued_bytes + packet_bytes(p) > QUEUE_BYTES) {
                    stream.dropped += stream.used; clear_queue(); stream.epoch++;
                }
                p->epoch = stream.epoch;
                stream.queue[stream.used++] = p; stream.queued_bytes += packet_bytes(p);
                pthread_cond_signal(&stream.available);
                pthread_mutex_unlock(&stream.mutex);
            }
            close(upstream);
        } else set_error("stock ADC TCP server is unavailable on localhost:18900");
        close(downstream);
        nf_stream_reset();
        pthread_mutex_lock(&nf_processor_mutex);
        nf_clear_results(stream.processor);
        pthread_mutex_unlock(&nf_processor_mutex);
        pthread_mutex_lock(&stream.mutex); stream.upstream = stream.downstream = -1; pthread_mutex_unlock(&stream.mutex);
    }
    return NULL;
}

typedef struct {
    float *a, *b;
    size_t capacity, size;
    uint64_t epoch, revision, position, previous;
    bool has_previous;
    bool calibrating, discard;
} collection;

static void feed(collection *window, packet *p) {
    nf_processor *processor = stream.processor;
    nf_config *config = &processor->config;
    pthread_mutex_lock(&stream.mutex); bool current = p->epoch == stream.epoch; pthread_mutex_unlock(&stream.mutex);
    if (!current) return;
    if (!processor->enabled) { window->size = 0; return; }
    for (unsigned r = 0; r < config->row_count; ++r)
        for (unsigned t = 0; t < config->rows[r].term_count; ++t)
            if (!p->channel[config->rows[r].terms[t].source]) {
                nf_clear_results(processor); set_error("a commanded formula requires an absent ADC channel"); return;
            }
    if (fabs(processor->sample_rate - p->rate) > 1.0) {
        nf_clear_results(processor); set_error("stream rate differs from commanded decimation"); return;
    }
    if (p->epoch != window->epoch || processor->revision != window->revision || p->lost ||
        (window->has_previous && p->pack_id != window->previous + 1)) {
        nf_clear_results(processor); window->size = 0; window->position = 0;
        window->epoch = p->epoch; window->revision = processor->revision;
        window->calibrating = processor->calibrating; window->discard = false;
    }
    if (window->calibrating != processor->calibrating) {
        window->size = 0; window->discard = true; window->calibrating = processor->calibrating;
    }
    window->previous = p->pack_id; window->has_previous = true;
    if (p->lost) return;
    unsigned first = p->channel[0] ? 0 : 1;
    unsigned char *head = p->channel[first];
    size_t count = (p->bytes[first] - HEADER) / (head[54] / 8);
    if (p->channel[0] && p->channel[1] &&
        (p->bytes[1] - HEADER) / (p->channel[1][54] / 8) != count) {
        nf_clear_results(processor); set_error("stream ADC channel lengths differ"); return;
    }
    bool continuous = !strcmp(config->window_mode, "continuous");
    size_t lock = continuous ? 0 : (size_t)nearbyint(config->lock_seconds * processor->sample_rate);
    size_t delay = (size_t)abs(config->b_delay);
    size_t modes = config->history_modes;
    if (continuous && processor->calibrating) {
        size_t reference_modes = (size_t)(0.2 * processor->sample_rate / processor->mode_size);
        if (!reference_modes) reference_modes = 1;
        if (modes > reference_modes) modes = reference_modes;
    }
    size_t collect = continuous ? modes * processor->mode_size + delay :
        (size_t)nearbyint(config->collect_seconds * processor->sample_rate);
    if (continuous && collect > 4194304) collect = ((4194304 - delay) / processor->mode_size) * processor->mode_size + delay;
    if (!collect || collect > 4194304) { set_error("stream collection size exceeds its memory limit"); return; }
    if (window->capacity != collect) {
        free(window->a); free(window->b);
        window->a = calloc(collect, sizeof(float)); window->b = calloc(collect, sizeof(float));
        window->capacity = collect; window->size = 0;
        if (!window->a || !window->b) {
            free(window->a); free(window->b); window->a = window->b = NULL; window->capacity = 0;
            set_error("cannot allocate streaming collection"); return;
        }
    }
    const unsigned char *raw[2] = {NULL, NULL};
    unsigned bits[2] = {0, 0};
    float scale[2] = {0, 0};
    for (unsigned channel = 0; channel < 2; ++channel) if (p->channel[channel]) {
        const unsigned char *h = p->channel[channel];
        raw[channel] = h + HEADER; bits[channel] = h[54];
        scale[channel] = 1.0f / (float)(1u << ((bits[channel] == 8 ? 8 : h[52]) - 1));
    }
    uint64_t initial_guard = (uint64_t)nearbyint(config->guard_seconds * processor->sample_rate);
    for (size_t i = 0; i < count;) {
        uint64_t position = window->position;
        if (continuous) {
            if (position < initial_guard) {
                size_t skip = initial_guard - position < count - i ? (size_t)(initial_guard - position) : count - i;
                i += skip; window->position += skip; continue;
            }
            if (window->discard) {
                size_t phase = (position - initial_guard) % processor->mode_size;
                if (phase) {
                    size_t skip = processor->mode_size - phase;
                    if (skip > count - i) skip = count - i;
                    i += skip; window->position += skip; continue;
                }
                window->discard = false;
            }
        } else {
            size_t phase = position % (lock + collect);
            if (phase == lock) { window->size = 0; window->discard = false; }
            if (phase < lock || window->discard) {
                size_t skip = phase < lock ? lock - phase : lock + collect - phase;
                if (skip > count - i) skip = count - i;
                i += skip; window->position += skip; continue;
            }
        }
        size_t take = collect - window->size;
        if (take > count - i) take = count - i;
        float *destinations[2] = {window->a + window->size, window->b + window->size};
        for (unsigned channel = 0; channel < 2; ++channel) {
            float *dest = destinations[channel];
            if (!raw[channel]) memset(dest, 0, take * sizeof(float));
            else if (bits[channel] == 16) {
                const unsigned char *source = raw[channel] + 2 * i;
                for (size_t j = 0; j < take; ++j)
                    dest[j] = (float)(int16_t)((unsigned)source[2*j] | ((unsigned)source[2*j+1] << 8)) * scale[channel];
            } else {
                const int8_t *source = (const int8_t *)raw[channel] + i;
                for (size_t j = 0; j < take; ++j) dest[j] = (float)source[j] * scale[channel];
            }
        }
        window->size += take; window->position += take; i += take;
        if (window->size == collect) {
            char error[NF_ERROR_SIZE] = {0};
            double guard = config->guard_seconds; unsigned end_guard = config->end_guard;
            if (continuous) {
                config->guard_seconds = 0; config->end_guard = 0;
            }
            if (nf_process(processor, window->a, window->b, collect, error)) set_error(error);
            else {
                pthread_mutex_lock(&stream.mutex); stream.results++; stream.error[0] = 0; pthread_mutex_unlock(&stream.mutex);
            }
            config->guard_seconds = guard; config->end_guard = end_guard;
            size_t shift = continuous ?
                (processor->calibrating ? collect - delay : ((collect / 2) / processor->mode_size) * processor->mode_size) : collect;
            if (!shift) shift = collect;
            window->size = collect - shift;
            if (window->size) {
                memmove(window->a, window->a + shift, window->size * sizeof(float));
                memmove(window->b, window->b + shift, window->size * sizeof(float));
            }
        }
    }
}
static void *worker(void *unused) {
    (void)unused; collection window = {0};
    while (!atomic_load(&stream.stop)) {
        pthread_mutex_lock(&stream.mutex);
        while (!stream.used && !atomic_load(&stream.stop)) pthread_cond_wait(&stream.available, &stream.mutex);
        packet *p = NULL;
        if (stream.used) {
            p = stream.queue[0]; memmove(stream.queue, stream.queue + 1, --stream.used * sizeof(packet *));
            stream.queued_bytes -= packet_bytes(p);
        }
        pthread_mutex_unlock(&stream.mutex);
        if (!p) continue;
        pthread_mutex_lock(&nf_processor_mutex);
        if (!atomic_load(&stream.stop)) feed(&window, p);
        pthread_mutex_unlock(&nf_processor_mutex);
        free_packet(p);
    }
    free(window.a); free(window.b); return NULL;
}

int nf_stream_start(nf_processor *processor, unsigned port, unsigned mask, char *error) {
    if (stream.running) {
        if (port == stream.port && mask == stream.mask) return 0;
        snprintf(error, NF_ERROR_SIZE, "stop streaming before changing its port or channel mask"); return -1;
    }
    if (port < 1024 || port > 65535 || mask < 1 || mask > 3) {
        snprintf(error, NF_ERROR_SIZE, "invalid streaming port or channel mask"); return -1;
    }
    int listener = socket(AF_INET, SOCK_STREAM, 0), one = 1;
    if (listener < 0) return -1;
    setsockopt(listener, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
    struct sockaddr_in address = {.sin_family = AF_INET, .sin_port = htons((uint16_t)port)};
    address.sin_addr.s_addr = htonl(INADDR_ANY);
    if (bind(listener, (struct sockaddr *)&address, sizeof(address)) || listen(listener, 1)) {
        snprintf(error, NF_ERROR_SIZE, "cannot listen on native streaming port %u", port); close(listener); return -1;
    }
    stream.processor = processor; stream.port = port; stream.mask = mask; stream.listener = listener;
    stream.epoch++; stream.packets = stream.dropped = stream.loss_packets = stream.results = 0;
    stream.error[0] = 0; atomic_store(&stream.stop, false);
    if (pthread_create(&stream.worker, NULL, worker, NULL)) { close(listener); return -1; }
    if (pthread_create(&stream.reader, NULL, reader, NULL)) {
        atomic_store(&stream.stop, true); pthread_cond_broadcast(&stream.available);
        pthread_join(stream.worker, NULL); close(listener); return -1;
    }
    stream.running = true; return 0;
}
void nf_stream_stop(void) {
    if (!stream.running) return;
    atomic_store(&stream.stop, true);
    pthread_mutex_lock(&stream.mutex);
    if (stream.upstream >= 0) shutdown(stream.upstream, SHUT_RDWR);
    if (stream.downstream >= 0) shutdown(stream.downstream, SHUT_RDWR);
    pthread_cond_broadcast(&stream.available); pthread_mutex_unlock(&stream.mutex);
    pthread_join(stream.reader, NULL); pthread_join(stream.worker, NULL); close(stream.listener);
    pthread_mutex_lock(&stream.mutex); clear_queue(); stream.running = false; stream.listener = -1; pthread_mutex_unlock(&stream.mutex);
}
bool nf_stream_running(void) { return stream.running; }
cJSON *nf_stream_status(void) {
    cJSON *status = cJSON_CreateObject(); pthread_mutex_lock(&stream.mutex);
    cJSON_AddBoolToObject(status, "running", stream.running);
    cJSON_AddNumberToObject(status, "port", stream.port);
    cJSON_AddNumberToObject(status, "packets", (double)stream.packets);
    cJSON_AddNumberToObject(status, "droppedPackets", (double)stream.dropped);
    cJSON_AddNumberToObject(status, "lossPackets", (double)stream.loss_packets);
    cJSON_AddNumberToObject(status, "results", (double)stream.results);
    cJSON_AddStringToObject(status, "error", stream.error);
    pthread_mutex_unlock(&stream.mutex); return status;
}
