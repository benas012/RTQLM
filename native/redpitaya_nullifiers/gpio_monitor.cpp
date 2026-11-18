/* Continuous E1.3 level observations. Timestamps use the board's CLOCK_REALTIME,
 * matching stock ADC capture timestamp epochs. No acquisition/output resets. */
#include <rp.h>
#include <time.h>
#include <csignal>
#include <cstdio>
#include <cstdint>
#include <cstdlib>
static volatile sig_atomic_t stop = 0;
static void stopped(int) { stop = 1; }
static uint64_t now(clockid_t clock) {
    timespec t{}; clock_gettime(clock,&t); return uint64_t(t.tv_sec)*1000000000+t.tv_nsec;
}
int main(int argc, char **argv) {
    char *end = nullptr;
    unsigned long period_us = argc > 1 ? std::strtoul(argv[1], &end, 10) : 500;
    if (argc > 2 || (argc > 1 && (!end || *end)) || period_us < 50 || period_us > 1000000) return 2;
    const uint64_t period_ns = period_us * uint64_t{1000};
    signal(SIGTERM,stopped); signal(SIGINT,stopped); signal(SIGHUP,stopped);
    signal(SIGPIPE, SIG_IGN); // Let the write fail and restore the saved direction.
    if (rp_InitReset(false) != RP_OK) return 2;
    rp_pinDirection_t direction;
    if (rp_DpinGetDirection(RP_DIO0_P,&direction) != RP_OK ||
        rp_DpinSetDirection(RP_DIO0_P,RP_IN) != RP_OK) return 3;
    uint64_t next=now(CLOCK_MONOTONIC);
    while (!stop) {
        timespec deadline{time_t(next/1000000000),long(next%1000000000)};
        clock_nanosleep(CLOCK_MONOTONIC,TIMER_ABSTIME,&deadline,nullptr);
        uint64_t first=now(CLOCK_REALTIME); rp_pinState_t state=RP_LOW;
        int status=rp_DpinGetState(RP_DIO0_P,&state);
        uint64_t last=now(CLOCK_REALTIME);
        if (std::printf("%llu,%llu,%d,%d\n",(unsigned long long)first,
                (unsigned long long)last,int(state),status)<0 || std::fflush(stdout)) break;
        next+=period_ns; uint64_t current=now(CLOCK_MONOTONIC);
        if (next<current) next+=((current-next)/period_ns+1)*period_ns;
    }
    return rp_DpinSetDirection(RP_DIO0_P,direction)==RP_OK ? 0 : 4;
}
