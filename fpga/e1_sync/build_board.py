from board_access import ROOT, connect, run

with connect() as client:
    run(client,"mkdir -p /root/rtqlm-e1-sync")
    with client.open_sftp() as sftp:
        sftp.put(str(ROOT/"board/capture_e1.cpp"),"/root/rtqlm-e1-sync/capture_e1.cpp")
    print(run(client,"cd /root/rtqlm-e1-sync && g++ -O3 -mcpu=cortex-a9 -mfpu=neon -std=c++20 "
              "-Wall -Wextra -Werror -Wpedantic -I/opt/redpitaya/include capture_e1.cpp "
              "-L/opt/redpitaya/lib -Wl,-rpath,/opt/redpitaya/lib "
              "-lrp -pthread -o capture-e1",timeout=90))
    print("Built ARM CH1/CH2/E1 capture program")
