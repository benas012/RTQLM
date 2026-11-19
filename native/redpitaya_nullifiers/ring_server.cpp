/* v0.94 continuous DMA producer. No calculation, FPGA loading or DAC changes. */
#include "acquire_rp.cpp"
#include <atomic>
#include <thread>
#include <sys/socket.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <poll.h>
extern "C" { volatile sig_atomic_t nf_stop_requested=0; }
static uint64_t clock_ns(clockid_t id) {
    timespec t{}; clock_gettime(id,&t); return uint64_t(t.tv_sec)*1000000000+t.tv_nsec;
}
static bool send_all(int fd,const void *data,size_t size) {
    auto p=static_cast<const char*>(data);
    while(size) { ssize_t n=send(fd,p,size,MSG_NOSIGNAL); if(n<=0) return false; p+=n;size-=n; }
    return true;
}
int main(int argc,char **argv) {
    if(argc!=3) return 2;
    unsigned dec=std::strtoul(argv[1],nullptr,10),port=std::strtoul(argv[2],nullptr,10);
    const char *token=std::getenv("NF_BUFFER_TOKEN");
    if((dec!=1 && dec!=2) || port<1024 || port>65535 || !token || strlen(token)!=64) return 2;
    char image[32]{}; FILE *info=fopen(NF_FPGA_INFO_PATH,"r");
    if(!info) return 2;
    int scanned=fscanf(info,"%31s",image);fclose(info);
    if(scanned!=1 || strcmp(image,"v0.94")) return 2;
    int lock=open(NF_LOCK_PATH,O_CREAT|O_RDWR,0600);
    if(lock<0 || flock(lock,LOCK_EX|LOCK_NB)) return 3;
    int listener=socket(AF_INET,SOCK_STREAM,0),one=1;
    setsockopt(listener,SOL_SOCKET,SO_REUSEADDR,&one,sizeof(one));
    sockaddr_in address{};address.sin_family=AF_INET;address.sin_port=htons(port);
    if(bind(listener,reinterpret_cast<sockaddr*>(&address),sizeof(address)) || listen(listener,1)) return 4;
    fprintf(stderr,"READY\n");fflush(stderr);
    pollfd pending{listener,POLLIN,0};
    if(poll(&pending,1,10000)<=0){close(listener);return 4;}
    int fd=accept(listener,nullptr,nullptr);close(listener);if(fd<0)return 4;
    timeval timeout{10,0};setsockopt(fd,SOL_SOCKET,SO_RCVTIMEO,&timeout,sizeof(timeout));
    setsockopt(fd,SOL_SOCKET,SO_SNDTIMEO,&timeout,sizeof(timeout));
    setsockopt(fd,IPPROTO_TCP,TCP_NODELAY,&one,sizeof(one));
    int send_size=1048576;setsockopt(fd,SOL_SOCKET,SO_SNDBUF,&send_size,sizeof(send_size));
    char authentication[65];size_t got=0;
    while(got<65) { auto n=recv(fd,authentication+got,65-got,0);if(n<=0)return 4;got+=n; }
    if(memcmp(authentication,token,64)||authentication[64]!='\n')return 4;
    if(rp_InitReset(false)!=RP_OK || rp_CalibInit()!=RP_OK)return 5;
    uint32_t base=0,bytes=0,old_dec=0;
    if(rp_AcqAxiGetMemoryRegion(&base,&bytes)!=RP_OK || rp_AcqAxiGetDecimationFactor(&old_dec)!=RP_OK)return 5;
    constexpr unsigned ring=4194304;
    if(bytes<ring*8)return 5;
    struct Cleanup { unsigned dec;~Cleanup(){rp_AcqStop();rp_AcqAxiEnable(RP_CH_1,false);
        rp_AcqAxiEnable(RP_CH_2,false);rp_AcqAxiSetDecimationFactor(dec);} } cleanup{old_dec};
    int status=rp_AcqStop();status|=rp_AcqReset();status|=rp_AcqSetTriggerSrc(RP_TRIG_SRC_DISABLED);
    status|=rp_AcqAxiSetDecimationFactor(dec);
    for(unsigned ch=0;ch<2;++ch){auto c=static_cast<rp_channel_t>(ch);
        status|=rp_AcqAxiSetBufferSamples(c,base+ch*ring*4,ring);
        status|=rp_AcqAxiSetTriggerDelay(c,ring);status|=rp_AcqAxiEnable(c,true);}
    if(status)return 5;
    ReadCalibration calibration[2];double zero[2],scale[2];
    for(unsigned ch=0;ch<2;++ch){if(!read_calibration(ch,calibration[ch]))return 5;
        auto c=calibration[ch];scale[ch]=double(c.gain)/c.base*c.full_scale/8192*c.input_gain;
        zero[ch]=c.offset-c.voltage_offset/scale[ch];}
    uint64_t start_before=clock_ns(CLOCK_REALTIME);
    if(rp_AcqStart()!=RP_OK)return 5;
    uint64_t start_after=clock_ns(CLOCK_REALTIME),anchor=start_before+(start_after-start_before)/2;
    std::atomic<uint64_t> written{0};std::atomic<bool> running{true},bad{false};
    double rate=125000000.0/dec;
    std::thread monitor([&]{uint32_t previous=0;uint64_t total=0,last=clock_ns(CLOCK_MONOTONIC);
        while(running){uint32_t position=0;uint64_t now=clock_ns(CLOCK_MONOTONIC);
            uint64_t wall_before=clock_ns(CLOCK_REALTIME);
            if(now-last>uint64_t(ring/rate*1e9)/2 || rp_AcqAxiGetWritePointer(RP_CH_1,&position)!=RP_OK){bad=true;break;}
            uint64_t wall_after=clock_ns(CLOCK_REALTIME);
            total+=(position+ring-previous)%ring;previous=position;
            // Do not let an NTP step or ADC/OS clock drift silently move the gate.
            uint64_t inferred=anchor+total*uint64_t(dec)*8;
            if(inferred+1000000<wall_before || inferred>wall_after+1000000){bad=true;break;}
            written=total;last=now;
            timespec delay{0,1000000};nanosleep(&delay,nullptr);}});
    unsigned count=unsigned(rate*.005);uint64_t cursor=0,sequence=0,lost=0,last_send_ns=0;
    std::vector<int16_t> payload(count*2);char error[NF_ERROR_SIZE]{};
    while(!bad){
        uint64_t available=written.load();
        if(available<cursor+count+4096){timespec pause{0,1000000};nanosleep(&pause,nullptr);continue;}
        // Leave half a ring of copy safety. Skip explicitly when transport falls behind.
        if(available-cursor>ring/2){uint64_t next=available-count-4096;lost+=next-cursor;cursor=next;}
        uint64_t copy_started=clock_ns(CLOCK_MONOTONIC);
        bool valid=true;
        for(unsigned ch=0;ch<2;++ch){nf_raw_channel raw{};
            if(read_raw_regions(ch,cursor%ring,count,raw,error,&calibration[ch])){valid=false;break;}
            size_t offset=ch*count;
            for(unsigned part=0;part<2;++part)if(raw.size[part]){
                memcpy(payload.data()+offset,raw.data[part],raw.size[part]*2);offset+=raw.size[part];}}
        if(!valid)break;
        // A delayed copy may have been overwritten. Never publish that buffer.
        if(bad || written.load()-cursor>=ring){lost+=count;cursor+=count;continue;}
        for(auto &word:payload){unsigned code=uint16_t(word)&16383;word=int(code&8191)-int(code&8192);}
        char header[768];int n=snprintf(header,sizeof(header),
            "{\"sequence\":%llu,\"firstSample\":%llu,\"timestampNs\":%llu,\"count\":%u,\"rate\":%.0f,\"lost\":%llu,\"zero\":[%.17g,%.17g],\"scale\":[%.17g,%.17g],\"anchorUncertaintyNs\":%llu,\"copyNs\":%llu,\"previousSendNs\":%llu}\n",
            (unsigned long long)sequence++,(unsigned long long)cursor,
            (unsigned long long)(anchor+cursor*uint64_t(dec)*8),count,rate,(unsigned long long)lost,
            zero[0],zero[1],scale[0],scale[1],(unsigned long long)(start_after-start_before),
            (unsigned long long)(clock_ns(CLOCK_MONOTONIC)-copy_started),(unsigned long long)last_send_ns);
        uint64_t send_started=clock_ns(CLOCK_MONOTONIC);
        if(n<0 || size_t(n)>=sizeof(header) || !send_all(fd,header,n) || !send_all(fd,payload.data(),payload.size()*2))break;
        last_send_ns=clock_ns(CLOCK_MONOTONIC)-send_started;
        cursor+=count;
    }
    running=false;monitor.join();close(fd);
    return bad?6:0;
}
