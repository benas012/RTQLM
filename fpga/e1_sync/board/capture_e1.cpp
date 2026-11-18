// Raw CH1/CH2/E1 capture for the RTQLM Release_2026.1 FPGA image.
// Packed DMA words preserve [13:0] ADC, [14] E1, [15] format marker.
#include <rp.h>
#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <unistd.h>
#include <signal.h>

static void check(int code, const char *name) {
    if (code != RP_OK) throw std::runtime_error(std::string(name)+" failed ("+std::to_string(code)+")");
}
#define CHECK(call) check((call), #call)

struct Hardware {
    int mem=-1;
    volatile uint32_t *hk=nullptr;
    bool sdk=false, started=false, enabled[2]={false,false}, saved=false;
    uint32_t control=0;
    ~Hardware() {
        if (sdk) {
            if (started) rp_AcqStop();
            if (enabled[0]) rp_AcqAxiEnable(RP_CH_1,false);
            if (enabled[1]) rp_AcqAxiEnable(RP_CH_2,false);
        }
        if (hk) {
            if (saved) hk[0x114/4]=control;
            munmap(const_cast<uint32_t *>(hk),4096);
        }
        if (mem>=0) close(mem);
        if (sdk) rp_Release();
    }
    void open() {
        mem=::open("/dev/mem",O_RDWR|O_SYNC);
        if (mem<0) throw std::runtime_error("open /dev/mem failed");
        void *p=mmap(nullptr,4096,PROT_READ|PROT_WRITE,MAP_SHARED,mem,0x40000000);
        if (p==MAP_FAILED) throw std::runtime_error("map housekeeping failed");
        hk=static_cast<volatile uint32_t *>(p);
        if (hk[0x110/4]!=0x45513101) throw std::runtime_error("RTQLM E1 FPGA signature missing");
        control=hk[0x114/4];saved=true;
        CHECK(rp_InitReset(false));sdk=true;
    }
};

static unsigned parse(const char *text,unsigned minimum,unsigned maximum) {
    char *end=nullptr;
    unsigned long value=std::strtoul(text,&end,10);
    if (!*text || !end || *end || value<minimum || value>maximum)
        throw std::runtime_error("invalid argument");
    return static_cast<unsigned>(value);
}

static int accept_client(unsigned port) {
    const char *token=std::getenv("RTQLM_E1_TOKEN");
    if (!token || std::strlen(token)!=64) throw std::runtime_error("64-character token required");
    int listener=socket(AF_INET,SOCK_STREAM,0), one=1;
    if (listener<0) throw std::runtime_error("socket failed");
    setsockopt(listener,SOL_SOCKET,SO_REUSEADDR,&one,sizeof(one));
    sockaddr_in addr{};addr.sin_family=AF_INET;addr.sin_port=htons(port);
    addr.sin_addr.s_addr=htonl(INADDR_ANY);
    if (bind(listener,reinterpret_cast<sockaddr *>(&addr),sizeof(addr)) || listen(listener,1)) {
        close(listener);throw std::runtime_error("listen failed");
    }
    std::fprintf(stderr,"READY\n");std::fflush(stderr);
    pollfd waiting{listener,POLLIN,0};
    if (poll(&waiting,1,30000)<=0) {
        close(listener);throw std::runtime_error("TCP client connection timeout");
    }
    int fd=accept(listener,nullptr,nullptr);close(listener);
    if(fd<0) throw std::runtime_error("accept failed");
    timeval timeout{30,0};
    setsockopt(fd,SOL_SOCKET,SO_RCVTIMEO,&timeout,sizeof(timeout));
    setsockopt(fd,SOL_SOCKET,SO_SNDTIMEO,&timeout,sizeof(timeout));
    setsockopt(fd,IPPROTO_TCP,TCP_NODELAY,&one,sizeof(one));
    int buffer=1024*1024;setsockopt(fd,SOL_SOCKET,SO_SNDBUF,&buffer,sizeof(buffer));
    char received[65];size_t at=0;
    while(at<sizeof(received)) {
        ssize_t n=recv(fd,received+at,sizeof(received)-at,0);
        if(n<=0){close(fd);throw std::runtime_error("authentication truncated");}
        at+=static_cast<size_t>(n);
    }
    if(std::memcmp(received,token,64) || received[64]!='\n') {
        close(fd);throw std::runtime_error("authentication failed");
    }
    return fd;
}

static void send_all(int fd,const void *data,size_t bytes) {
    auto p=static_cast<const char *>(data);
    while(bytes) {
        ssize_t n=send(fd,p,bytes,MSG_NOSIGNAL);
        if(n<=0) throw std::runtime_error("TCP send failed");
        p+=n;bytes-=static_cast<size_t>(n);
    }
}

int main(int argc,char **argv) {
    signal(SIGPIPE,SIG_IGN);
    try {
        unsigned samples=6250000,port=18913,delay=5;
        bool diagnostic=false;
        for(int i=1;i<argc;++i) {
            if(!std::strcmp(argv[i],"--samples") && i+1<argc) samples=parse(argv[++i],64,8388544);
            else if(!std::strcmp(argv[i],"--port") && i+1<argc) port=parse(argv[++i],1024,65535);
            else if(!std::strcmp(argv[i],"--delay") && i+1<argc) delay=parse(argv[++i],0,127);
            else if(!std::strcmp(argv[i],"--diagnostic")) diagnostic=true;
            else throw std::runtime_error("usage: capture-e1 [--samples N] [--port N] [--delay N] [--diagnostic]");
        }
        unsigned stored=(samples+63)/64*64;
        Hardware h;h.open();
        h.hk[0x114/4]=(delay<<8)|1u|(diagnostic?2u:0u);
        CHECK(rp_DpinSetDirection(RP_DIO0_P,RP_IN));
        CHECK(rp_AcqStop());CHECK(rp_AcqReset());
        CHECK(rp_AcqSetTriggerSrc(RP_TRIG_SRC_DISABLED));
        CHECK(rp_AcqAxiSetDecimationFactor(1));
        uint32_t address=0,bytes=0;CHECK(rp_AcqAxiGetMemoryRegion(&address,&bytes));
        uint32_t half=(bytes/2)&~4095u;
        if(uint64_t(stored)*2>half) throw std::runtime_error("capture exceeds reserved DMA memory");
        for(unsigned i=0;i<2;++i) {
            auto ch=static_cast<rp_channel_t>(i);
            CHECK(rp_AcqAxiEnable(ch,false));
            CHECK(rp_AcqAxiSetBufferSamples(ch,address+i*half,stored));
            CHECK(rp_AcqAxiSetTriggerDelay(ch,static_cast<int32_t>(stored)));
            CHECK(rp_AcqAxiEnable(ch,true));h.enabled[i]=true;
        }
        int fd=accept_client(port);
        auto begin=std::chrono::steady_clock::now();
        CHECK(rp_AcqStart());h.started=true;
        CHECK(rp_AcqSetTriggerSrc(RP_TRIG_SRC_NOW));
        bool filled[2]={false,false};
        while(!filled[0] || !filled[1]) {
            for(unsigned i=0;i<2;++i)
                CHECK(rp_AcqAxiGetBufferFillState(static_cast<rp_channel_t>(i),&filled[i]));
            if(std::chrono::steady_clock::now()-begin>std::chrono::seconds(5))
                throw std::runtime_error("DMA fill timeout");
            std::this_thread::sleep_for(std::chrono::microseconds(100));
        }
        CHECK(rp_AcqStop());h.started=false;
        auto captured=std::chrono::steady_clock::now();
        std::vector<int16_t> data[2];uint32_t positions[2]{};
        for(unsigned i=0;i<2;++i) {
            auto ch=static_cast<rp_channel_t>(i);
            CHECK(rp_AcqAxiGetWritePointerAtTrig(ch,&positions[i]));
            std::vector<std::span<int16_t>> regions;
            CHECK(rp_AcqAxiGetDataRawDirect(ch,positions[i],samples,&regions));
            data[i].reserve(samples);
            for(auto region:regions) data[i].insert(data[i].end(),region.begin(),region.end());
            if(data[i].size()!=samples) throw std::runtime_error("short DMA read");
        }
        auto copied=std::chrono::steady_clock::now();
        uint32_t record_errors=0;
        for(unsigned n=0;n<samples;++n) {
            auto a=static_cast<uint16_t>(data[0][n]),b=static_cast<uint16_t>(data[1][n]);
            record_errors|=((~(a&b))&0x8000u)|((a^b)&0x4000u);
        }
        if(record_errors) throw std::runtime_error("record markers or channel E1 tags disagree");
        auto checked=std::chrono::steady_clock::now();
        double elapsed=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-begin).count();
        char header[1024];
        int length=std::snprintf(header,sizeof(header),
            "{\"ok\":true,\"format\":\"rtqlm-e1-tagged-v1\",\"count\":%u,\"storedCount\":%u,"
            "\"sampleRateHz\":125000000,\"channels\":[\"CH1\",\"CH2\",\"E1_PIN3\"],"
            "\"payloadBytes\":%llu,\"payloadLayout\":\"planar-u16-le-tagged\","
            "\"diagnostic\":%s,\"adcUnits\":\"uncalibrated-signed-14-bit-counts\","
            "\"e1DelayTap\":%u,\"physicalTimingOffsetVerified\":false,"
            "\"triggerPositions\":[%u,%u],\"acquireAndReadMs\":%.9g,"
            "\"acquireMs\":%.9g,\"readAndCopyMs\":%.9g,\"integrityCheckMs\":%.9g}\n",
            samples,stored,static_cast<unsigned long long>(samples)*4,
            diagnostic?"true":"false",delay,positions[0],positions[1],elapsed,
            std::chrono::duration<double,std::milli>(captured-begin).count(),
            std::chrono::duration<double,std::milli>(copied-captured).count(),
            std::chrono::duration<double,std::milli>(checked-copied).count());
        send_all(fd,header,static_cast<size_t>(length));
        for(auto &rail:data) send_all(fd,rail.data(),rail.size()*sizeof(int16_t));
        shutdown(fd,SHUT_WR);close(fd);
        return 0;
    } catch(const std::exception &e) {
        std::fprintf(stderr,"ERROR: %s\n",e.what());return 1;
    }
}
