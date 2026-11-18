// Read current generators and E1 pin 3 without resetting or driving anything.
#include <rp.h>
#include <cstdio>
#include <initializer_list>
#include <cstdint>
#include <fcntl.h>
#include <sys/mman.h>
#include <unistd.h>

int main() {
    if (rp_InitReset(false) != RP_OK) return 1;
    int status = 0;
    for (auto channel : {RP_CH_1, RP_CH_2}) {
        bool enabled = false;
        float frequency = 0, amplitude = 0, offset = 0, phase = 0;
        rp_waveform_t waveform{};
        status |= rp_GenOutIsEnabled(channel, &enabled);
        status |= rp_GenGetFreq(channel, &frequency);
        status |= rp_GenGetAmp(channel, &amplitude);
        status |= rp_GenGetOffset(channel, &offset);
        status |= rp_GenGetPhase(channel, &phase);
        status |= rp_GenGetWaveform(channel, &waveform);
        std::printf("{\"channel\":%d,\"enabled\":%s,\"frequency\":%.9g,\"amplitude\":%.9g,"
                    "\"offset\":%.9g,\"phase\":%.9g,\"waveform\":%d}\n",
                    channel + 1, enabled ? "true" : "false", frequency, amplitude,
                    offset, phase, waveform);
    }
    rp_pinState_t value{};
    rp_pinDirection_t direction{};
    status |= rp_DpinGetState(RP_DIO0_P, &value);
    status |= rp_DpinGetDirection(RP_DIO0_P, &direction);
    std::printf("{\"e1Pin3State\":%d,\"e1Pin3Direction\":%d}\n", value, direction);
    int fd=open("/dev/mem",O_RDONLY|O_SYNC);
    if (fd<0) status=1;
    else {
        void *p=mmap(nullptr,4096,PROT_READ,MAP_SHARED,fd,0x40000000);
        if (p==MAP_FAILED) status=1;
        else {
            auto hk=static_cast<volatile uint32_t *>(p);
            std::printf("{\"fpgaSignature\":%u,\"e1Control\":%u}\n",hk[0x110/4],hk[0x114/4]);
            munmap(p,4096);
        }
        close(fd);
    }
    status |= rp_Release();
    return status ? 1 : 0;
}
