/* Ownership and lifetime checks for reusable, publicly owned projection output. */
#include "nullifier.h"
#include <assert.h>
#include <stdlib.h>
#include <stdio.h>

int main(void) {
    nf_processor *p = calloc(1, sizeof(*p));
    nf_config c; nf_defaults(&c);
    c.capture_samples = 4096; c.guard_seconds = 0; c.end_guard = 0; c.b_delay = 0;
    c.history_modes = 500;
    char error[NF_ERROR_SIZE];
    assert(p && !nf_configure(p, &c, error));
    int16_t words[4096];
    for (unsigned i = 0; i < 4096; ++i) words[i] = (int16_t)(i % 173);
    nf_raw_channel raw[2] = {{{words, NULL}, {4096, 0}, 0, NULL},
                             {{words, NULL}, {4096, 0}, 0, NULL}};
    double *first[2] = {NULL, NULL};
    for (unsigned capture = 0; capture < 5; ++capture) {
        double *q[2]; size_t modes;
        assert(!nf_project_raw(p, raw, 4096, q, &modes, error));
        assert(q[0] != p->quadratures[0] && q[1] != p->quadratures[1]);
        assert(!p->spare_quadratures[0] && !p->spare_quadratures[1]);
        if (!capture) { first[0] = q[0]; first[1] = q[1]; }
        else if (!(capture % 2)) assert(q[0] == first[0] && q[1] == first[1]);
        assert(!nf_process_modes(p, q, modes, error));
    }
    nf_start_calibration(p);
    assert(!p->quadratures[0] && !p->spare_quadratures[0]);
    double *calibrating[2] = {NULL, NULL};
    for (unsigned capture = 0; capture < 3; ++capture) {
        double *q[2]; size_t modes;
        assert(!nf_project_raw(p, raw, 4096, q, &modes, error));
        if (!capture) { calibrating[0] = q[0]; calibrating[1] = q[1]; }
        else assert(q[0] == calibrating[0] && q[1] == calibrating[1]);
        assert(!nf_process_modes(p, q, modes, error));
    }
    nf_clear_results(p);
    assert(!p->spare_quadratures[0] && !p->spare_capacity);
    double *oversize[2] = {calloc(501, sizeof(double)), calloc(501, sizeof(double))};
    assert(oversize[0] && oversize[1]);
    assert(nf_process_modes(p, oversize, 501, error) != 0);
    nf_destroy(p); free(p);
    puts("PASS: quadrature reuse, calibration reuse, ownership, reset and capacity validation");
    return 0;
}
