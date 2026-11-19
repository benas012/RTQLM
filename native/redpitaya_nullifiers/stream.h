#ifndef RTQLM_STREAM_H
#define RTQLM_STREAM_H
#include "nullifier.h"
#include "vendor/cJSON.h"
#include <pthread.h>

extern pthread_mutex_t nf_processor_mutex;
int nf_stream_start(nf_processor *processor, unsigned port, unsigned channel_mask, char *error);
void nf_stream_stop(void);
void nf_stream_reset(void);
bool nf_stream_running(void);
cJSON *nf_stream_status(void);
#endif
