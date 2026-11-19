"""Receive exactly aligned CH1/CH2/E1 records from the custom image over TCP."""
import argparse
import json
from pathlib import Path
import secrets
import socket
import time

import numpy as np

if __package__:
    from .board_access import ROOT, HOST, connect
else:
    from board_access import ROOT, HOST, connect


def receive_exact(stream, size):
    result=bytearray(size)
    view=memoryview(result)
    at=0
    while at<size:
        count=stream.readinto(view[at:])
        if not count:
            raise ConnectionError("Truncated capture")
        at+=count
    return result


def decode_payload(payload, samples, diagnostic=False):
    """Split atomic FPGA records; reject damaged or misaligned captures."""
    if len(payload) != samples * 4 or samples < 1:
        raise ValueError("Invalid capture payload length")
    words=np.frombuffer(payload,dtype="<u2").reshape(2,samples)
    if not np.all(words&0x8000):
        raise ValueError("Missing FPGA record marker")
    if np.any((words[0]^words[1])&0x4000):
        raise ValueError("E1 does not match between ADC channel records")
    analog=((words&0x3fff).astype(np.int16)<<2)>>2
    e1=((words[0]>>14)&1).astype(np.uint8)
    if diagnostic:
        if (np.any(((words[0]+words[1])&0x3fff) != 0x3fff)
                or np.any(e1 != ((words[0]>>5)&1))
                or np.any((np.diff(words[0].astype(np.int32)&0x3fff)&0x3fff)!=1)):
            raise ValueError("Diagnostic sample sequence or E1 alignment failed")
    return analog[0],analog[1],e1


def capture(samples=6250000, diagnostic=False, delay=5, port=18913):
    call_start=time.perf_counter()
    if not 64 <= samples <= 8388544 or not 0 <= delay <= 127:
        raise ValueError("Invalid sample count or E1 delay")
    token=secrets.token_hex(32)
    with connect() as client:
        command=("RTQLM_E1_TOKEN="+token+" /root/rtqlm-e1-sync/capture-e1 --samples "
                 +str(samples)+" --delay "+str(delay)+" --port "+str(port)
                 +(" --diagnostic" if diagnostic else ""))
        _, process, errors=client.exec_command(command,timeout=30)
        line=errors.readline(1024).strip()
        if line!="READY":
            raise RuntimeError(line+" "+errors.read().decode())
        address=socket.getaddrinfo(HOST,port,socket.AF_INET,socket.SOCK_STREAM)[0][4]
        started=time.perf_counter()
        with socket.create_connection(address,timeout=30) as connection:
            connection.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
            connection.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,4*1024*1024)
            connection.sendall(token.encode()+b"\n")
            with connection.makefile("rb") as stream:
                header=stream.readline(4096)
                if not header:
                    raise RuntimeError("Board capture failed: "+errors.read().decode())
                metadata=json.loads(header)
                if (metadata.get("format")!="rtqlm-e1-tagged-v1" or metadata["count"]!=samples
                        or metadata["payloadBytes"]!=samples*4):
                    raise ValueError("Invalid capture header")
                payload=receive_exact(stream,samples*4)
        metadata["captureAndTransferMs"]=(time.perf_counter()-started)*1000
        status=process.channel.recv_exit_status()
        if status:
            raise RuntimeError(errors.read().decode())
    decode_start=time.perf_counter()
    a,b,e1=decode_payload(payload,samples,diagnostic)
    metadata["hostDecodeMs"]=(time.perf_counter()-decode_start)*1000
    metadata["totalCallMs"]=(time.perf_counter()-call_start)*1000
    return a,b,e1,metadata


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples",type=int,default=6250000)
    parser.add_argument("--diagnostic",action="store_true")
    parser.add_argument("--delay",type=int,default=5)
    parser.add_argument("--output",type=Path,default=ROOT/"captures/latest.npz")
    args=parser.parse_args()
    a,b,e,metadata=capture(args.samples,args.diagnostic,args.delay)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    np.savez(args.output,CH1=a,CH2=b,E1_PIN3=e,metadata=json.dumps(metadata))
    print(json.dumps({**metadata,"e1High":int(e.sum()),"e1Low":int(len(e)-e.sum()),
                     "ch1Range":[int(a.min()),int(a.max())],
                     "ch2Range":[int(b.min()),int(b.max())],"saved":str(args.output)},indent=2))
