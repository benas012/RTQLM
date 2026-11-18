"""Continuous v0.94 DMA transport; no stock streaming application or FPGA switch."""
import asyncio
import json
import math
import secrets
import socket
import time
from urllib.parse import urlsplit
import numpy as np
import paramiko
from redpitaya_acquisition.acquisition.source import AcquisitionSource
from redpitaya_acquisition.domain.frame import RawSampleFrame


class RingStreamingSource(AcquisitionSource):
    def __init__(self,profile):
        self.profile=profile
        self.host=urlsplit(profile.host if '://' in profile.host else 'http://'+profile.host).hostname
        self.client=None;self.connection=None;self.reader=None;self.running=False
        self.stream_id=secrets.randbits(63);self.previous=None

    @property
    def module_name(self):return 'v0.94-continuous-dma'

    def output_status(self):
        result = super().output_status()
        result.update(state='unknown',
                      error='Continuous DMA leaves outputs unchanged; output control is unavailable')
        return result

    def _connect(self):
        client=paramiko.SSHClient();client.load_system_host_keys()
        client.connect(self.host,username='root',timeout=10)
        _,out,_=client.exec_command('cat /tmp/loaded_fpga.inf',timeout=5)
        if out.read().decode().strip()!='v0.94':
            client.close();raise RuntimeError('Continuous DMA requires existing v0.94; FPGA changes are disabled')
        self.client=client

    async def connect(self):await asyncio.to_thread(self._connect)

    def _start(self):
        token=secrets.token_hex(32)
        _,_,err=self.client.exec_command('NF_BUFFER_TOKEN='+token+
            ' /root/rtqlm-buffered-server-20261101/ring-server '+str(self.profile.decimation)+' 18904',timeout=10)
        if err.readline().strip()!='READY':raise RuntimeError('Continuous DMA worker did not start')
        # Reuse the SSH peer address; avoid a second mDNS lookup during startup.
        peer = self.client.get_transport().sock.getpeername()[0]
        if ':' in peer:  # The installed DMA listener is IPv4; SSH may select IPv6.
            peer = socket.getaddrinfo(self.host, 18904, socket.AF_INET,
                                      socket.SOCK_STREAM)[0][4][0]
        self.connection=socket.create_connection((peer,18904),timeout=10)
        self.connection.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,4*1024*1024)
        self.connection.sendall(token.encode()+b'\n')
        self.reader=self.connection.makefile('rb')
        self.previous=None;self.stream_id=secrets.randbits(63);self.running=True

    async def start(self):await asyncio.to_thread(self._start)

    async def stop(self):
        self.running=False
        if self.connection:
            try:self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:pass
            self.connection.close();self.connection=None
        if self.reader:self.reader.close();self.reader=None

    async def disconnect(self):
        await self.stop()
        if self.client:self.client.close();self.client=None

    def _read(self):
        line=self.reader.readline(2049)
        if not line or len(line)>2048:raise ConnectionError('DMA stream closed or invalid metadata')
        meta=json.loads(line);count=int(meta['count']);rate=125000000/self.profile.decimation
        if (count!=round(rate*.005) or meta['rate']!=rate
                or not 0 <= meta['anchorUncertaintyNs'] <= 1000000
                or any(type(meta[key]) is not int or meta[key] < 0
                       for key in ('sequence', 'firstSample', 'timestampNs', 'lost'))
                or len(meta['zero']) != 2 or len(meta['scale']) != 2
                or not all(math.isfinite(value) for value in meta['zero'] + meta['scale'])
                or not all(value > 0 for value in meta['scale'])):
            raise ValueError('Invalid DMA timing metadata')
        if self.previous and (meta['sequence'] <= self.previous['sequence']
                or meta['firstSample'] < self.previous['firstSample'] + count
                or meta['lost'] < self.previous['lost']):
            raise ValueError('DMA sample timeline moved backwards')
        payload=self.reader.read(count*4)
        if len(payload)!=count*4:raise ConnectionError('Truncated DMA block')
        channels=np.frombuffer(payload,dtype='<i2').reshape(2,count)
        gap=self.previous is not None and (meta['sequence']!=self.previous['sequence']+1 or
            meta['firstSample']!=self.previous['firstSample']+self.previous['count'])
        self.previous=meta
        return RawSampleFrame(stream_id=self.stream_id,sequence=meta['sequence'],source_pack_id=meta['sequence'],
            captured_at_unix_ns=time.time_ns(),adc_capture_unix_ns=meta['timestampNs'],
            base_rate_hz=125000000,decimation=self.profile.decimation,sample_rate_hz=rate,
            samples_per_channel=count,channel_mask=3,channel_1=channels[0],channel_2=channels[1],
            fpga_lost_ch1=meta['lost'],fpga_lost_ch2=meta['lost'],packet_gap_detected=gap,
            adc_zero_ch1=meta['zero'][0],adc_zero_ch2=meta['zero'][1],
            adc_scale_ch1=meta['scale'][0],adc_scale_ch2=meta['scale'][1])

    async def frames(self):
        while self.running:
            frame=await asyncio.to_thread(self._read)
            if self.running:yield frame
