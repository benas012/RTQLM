"""Engine-host streaming analysis gated by E1.3 observations in board time."""
from collections import deque
from dataclasses import replace
import threading
import time
from urllib.parse import urlsplit

import paramiko
import numpy as np

from .gate_timeline import GateObservation, high_sample_ranges
from .nullifiers import DeviceNullifierProcessor


class GatedStreamingProcessor(DeviceNullifierProcessor):
    backend = 'server-stream'

    def __init__(self,host,window_ms):
        super().__init__()
        self.host=urlsplit(host if '://' in host else 'http://'+host).hostname
        self._processor.configure(replace(self._processor.config,
            collect_seconds=window_ms/1000,lock_seconds=0,window_mode='periodic'))
        self._observations=deque(maxlen=30000)
        self._observation_lock=threading.Lock()
        self._monitor=None
        self._monitor_thread=None
        self._gate_error='E1 monitor not started'
        self._accepted_until=None
        self.gate_discarded_samples=0
        self.gate_accepted_samples=0
        # Conservative guard, not a measured hardware synchronization guarantee.
        self.clock_margin_ns=1_000_000
        self._lost_counters=None

    def prepare_stream(self):
        if self._monitor is not None: return
        client=paramiko.SSHClient(); client.load_system_host_keys()
        client.connect(self.host,username='root',timeout=10)
        _,stdout,_=client.exec_command('/root/rtqlm-buffered-server-20261101/gpio-monitor',timeout=5)
        self._monitor=client
        def read():
            try:
                for line in stdout:
                    first,last,state,status=map(int,line.split(','))
                    observation=GateObservation(first,last,state==1,status==0 and state in (0,1))
                    with self._observation_lock:
                        if self._observations and first<self._observations[-1].latest_ns:
                            self._observations.clear()
                        self._observations.append(observation)
                    self._gate_error=None
            except Exception as exc: self._gate_error=str(exc)
            finally: self._gate_error=self._gate_error or 'E1 monitor disconnected'
        self._monitor_thread=threading.Thread(target=read,daemon=True,name='e1-gate-monitor')
        self._monitor_thread.start()

    def close(self):
        if self._monitor is not None: self._monitor.close()
        self._monitor=None
        if self._monitor_thread: self._monitor_thread.join(timeout=2)
        with self._observation_lock: self._observations.clear()
        self._accepted_until=None

    def _feed(self,frame):
        counters=(frame.fpga_lost_ch1,frame.fpga_lost_ch2)
        loss=counters!=self._lost_counters and any(counters)
        self._lost_counters=counters
        start=frame.adc_capture_unix_ns
        stop=start+round(frame.samples_per_channel*1e9/frame.sample_rate_hz)
        # GPIO transport can arrive after the ADC packet. Wait briefly for a
        # bracketing observation; never extrapolate a previous HIGH into the future.
        deadline=time.monotonic()+0.025
        while time.monotonic()<deadline:
            with self._observation_lock: observations=list(self._observations)
            if observations and observations[-1].earliest_ns>=stop+self.clock_margin_ns: break
            if self._gate_error: break
            time.sleep(.001)
        ranges=[]
        if start>0 and not self._gate_error:
            ranges=high_sample_ranges(observations,first_sample_ns=start,
                sample_count=frame.samples_per_channel,sample_rate_hz=frame.sample_rate_hz,
                clock_uncertainty_ns=self.clock_margin_ns)
        accepted=sum(b-a for a,b in ranges)
        self.gate_accepted_samples+=accepted
        self.gate_discarded_samples+=frame.samples_per_channel-accepted
        if not ranges:
            self._processor.reset_stream(); self._clear_result()
            self._accepted_until=None
            self._error=self._gate_error or ('ADC timestamps unavailable' if not start else 'E1 LOW or timing uncertain')
            return
        for a,b in ranges:
            first=start+round(a*1e9/frame.sample_rate_hz)
            gap=loss or self._accepted_until is None or abs(first-self._accepted_until)>round(1e9/frame.sample_rate_hz)
            if gap:
                self._processor.reset_stream(); self._clear_result()
            selected=replace(frame,channel_1=frame.channel_1[a:b] if frame.channel_1 is not None else None,
                channel_2=frame.channel_2[a:b] if frame.channel_2 is not None else None,
                samples_per_channel=b-a,adc_capture_unix_ns=first,
                fpga_lost_ch1=0,fpga_lost_ch2=0,
                packet_gap_detected=frame.packet_gap_detected or gap)
            super()._feed(selected)
            self._accepted_until=start+round(b*1e9/frame.sample_rate_hz)
        if ranges[-1][1]<frame.samples_per_channel:
            self._accepted_until=None

    def status(self):
        with self._observation_lock:
            observations = list(self._observations)
        intervals = np.diff([o.earliest_ns for o in observations]) / 1e6
        intervals = intervals[intervals >= 0]
        polling = ({'samples': len(observations),
                    'meanMs': float(np.mean(intervals)),
                    'p95Ms': float(np.percentile(intervals, 95)),
                    'maxMs': float(np.max(intervals)),
                    'over2Ms': int(np.count_nonzero(intervals > 2))}
                   if len(intervals) else {'samples': len(observations)})
        return {**super().status(),'backend':self.backend,'gatePin':'DIO0_P',
                'gatePolling': polling,
                'gatePollIntervalMs':1,'gateClockMarginMs':self.clock_margin_ns/1e6,
                'gateAcceptedSamples':self.gate_accepted_samples,
                'gateDiscardedSamples':self.gate_discarded_samples,'gateError':self._gate_error}
