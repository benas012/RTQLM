#include "../nullifier.h"
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(void) {
    nf_processor *p = calloc(1, sizeof(*p));
    nf_config c; nf_defaults(&c);
    c.b_delay = 0; c.guard_seconds = 0; c.end_guard = 0;
    strcpy(c.shape, "identity");
    char error[NF_ERROR_SIZE] = {0};
    assert(nf_configure(p, &c, error) == 0);
    assert(p->mode_size == 38 && p->mode_size_max == 39 && p->mode_step == 38.5);
    for (uint64_t k = 0; k < 1000000; ++k) {
        assert(nf_mode_boundary(p, k) == (k * 77) / 2);
        unsigned size = (unsigned)(nf_mode_boundary(p, k + 1) - nf_mode_boundary(p, k));
        assert(size == (k % 2 ? 39 : 38));
    }
    assert(nf_mode_count(p, 625024, 0) == 16234);
    assert(nf_mode_count(p, 38, 0) == 1);
    assert(nf_mode_count(p, 38, 1) == 0);
    double small = 0, large = 0;
    for (unsigned j = 0; j < 38; ++j) small += p->mode[j] * p->mode[j] / p->sample_rate;
    for (unsigned j = 0; j < 39; ++j) large += p->mode_large[j] * p->mode_large[j] / p->sample_rate;
    assert(fabs(small - 1) < 1e-12 && fabs(large - 1) < 1e-12);
    c.mode_width = 0.310 * 1e-6;
    assert(nf_configure(p, &c, error) == 0);
    for (uint64_t k = 0; k < 1000000; ++k)
        assert(nf_mode_boundary(p, k) == (k * 155) / 4);
    c.history_modes = 100;
    assert(nf_configure(p, &c, error) == 0);
    assert(p->raw_boundary_count == NF_DEFAULT_HISTORY);
    c.decimation = 2;
    assert(nf_configure(p, &c, error) == 0);
    assert(p->raw_boundary_count == 100 && nf_mode_boundary(p, 8) == 155);
    c.decimation = 1;
    c.mode_width = 0.304 * 1e-6;
    assert(nf_configure(p, &c, error) == 0 && nf_mode_boundary(p, 100) == 3800);
    c.decimation = 24;
    c.mode_width = 7.36e-6;
    assert(nf_configure(p, &c, error) == 0);
    uint64_t far = UINT64_C(1000000000000001);
    assert(nf_mode_boundary(p, far) == far * 115 / 3);
    assert(nf_mode_count(p, 115, far) == 3 && nf_mode_count(p, 114, far) == 2);
    nf_destroy(p); free(p);
    puts("1,000,000 bin boundaries and both noise gains verified");
    return 0;
}
