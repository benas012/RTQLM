// Restore the saved sine-generator settings after FPGA reconfiguration.
#include <rp.h>
#include <cstdio>
#include <cstdlib>

int main(int argc, char **argv) {
    if (argc != 13 || rp_InitReset(false) != RP_OK) return 1;
    int status = rp_GenReset();
    for (int i=0; i<2; ++i) {
        auto ch = static_cast<rp_channel_t>(i);
        char **p = argv + 1 + i*6;
        // Only a saved sine waveform is supported by this task-local restorer.
        if (std::atoi(p[4]) != RP_WAVEFORM_SINE) { rp_Release(); return 2; }
        status |= rp_GenOutDisable(ch);
        status |= rp_GenMode(ch, RP_GEN_MODE_CONTINUOUS);
        status |= rp_GenWaveform(ch, RP_WAVEFORM_SINE);
        status |= rp_GenFreq(ch, std::strtof(p[0], nullptr));
        status |= rp_GenAmp(ch, std::strtof(p[1], nullptr));
        status |= rp_GenOffset(ch, std::strtof(p[2], nullptr));
        status |= rp_GenPhase(ch, std::strtof(p[3], nullptr));
        if (std::atoi(p[5])) {
            status |= rp_GenOutEnable(ch);
            status |= rp_GenTriggerOnly(ch);
        }
    }
    status |= rp_Release();
    std::printf("Restored saved output settings; status=%d\n", status);
    return status ? 1 : 0;
}
