"""Buffered ADC transfer over TCP (or SSH fallback), with host-side C processing."""
from __future__ import annotations

import json
import shlex
import subprocess
import time
import socket
import secrets
from contextlib import suppress

import paramiko

from .board_nullifiers import BoardNullifierProcessor


class ServerNullifierProcessor(BoardNullifierProcessor):
    backend = 'buffered-server-c'

    def _latest_payload(self, reply, config):
        result = super()._latest_payload(reply, config)
        result['status'] = ('Buffered capture calculated in C on server; gaps between captures; '
                            'waveform shows the first 1024 samples.')
        return result

    def __init__(self, host, executable, decimation, channel_mask, port, *, server_command,
                 transport='tcp', binary_port=18903):
        super().__init__(host, executable, decimation, channel_mask, port)
        self.server_command = server_command
        self._server = None
        self.buffered = True
        self.transport, self.binary_port = transport, binary_port
        self._socket = None
        self._capture_sequence = 0
        self.measurement_window_ms = 5.0

    @staticmethod
    def _exchange(stdin, stdout, command, payload=b''):
        stdin.write(json.dumps(command).encode() + b'\n')
        if payload:
            stdin.write(payload)
        stdin.flush()
        line = stdout.readline(64*1024*1024+1)
        if not line or len(line) > 64*1024*1024:
            raise ConnectionError('Binary worker disconnected or returned an oversized reply')
        result = json.loads(line)
        if not result.get('ok'):
            raise ValueError(result.get('error', 'Binary worker rejected command'))
        return result

    def _connect(self):
        if self._client is not None:
            return
        client = paramiko.SSHClient(); client.load_system_host_keys()
        try:
            client.connect(self.host, username='root', timeout=10)
            if self._remote_command(client, 'cat /tmp/loaded_fpga.inf') != 'v0.94':
                raise RuntimeError('Server buffered acquisition requires existing v0.94 FPGA')
            self._ensure_installed(client, 'rp-buffered-board')
            self._server = subprocess.Popen(self.server_command, stdin=subprocess.PIPE,
                                            stdout=subprocess.PIPE)
            if self.transport == 'tcp':
                token = secrets.token_hex(32)
                _, _, stderr = client.exec_command(
                    'NF_BUFFER_TOKEN='+token+' '+shlex.quote(self.executable)+
                    ' --listen '+str(self.binary_port), timeout=15)
                if stderr.readline().strip() != 'READY':
                    raise RuntimeError('Board binary TCP listener did not start')
                self._socket = socket.create_connection((self.host,self.binary_port),timeout=15)
                self._socket.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
                self._socket.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,4*1024*1024)
                self._socket.sendall(token.encode()+b'\n')
                self._stdin = self._socket.makefile('wb')
                self._stdout = self._socket.makefile('rb')
            else:
                self._stdin, self._stdout, _ = client.exec_command(shlex.quote(self.executable), timeout=15)
            self._client = client
            self._call({'command':'configure','config':self._settings(self.config)})
            self._analysis_error = None
        except Exception:
            client.close(); self.close(); raise

    def prepare_capture(self):
        with self._lock:
            self._connect()

    def _call(self, command):
        try:
            return self._call_connected(command)
        except Exception:
            self.close()
            self._analysis_error = 'Server processing session lost; reconnect and recalibrate'
            raise

    def _call_connected(self, command):
        if self._server is None or self._client is None:
            raise RuntimeError('Server processing session is disconnected')
        kind = command['command']
        if kind == 'configure':
            self._exchange(self._stdin, self._stdout, command)
        if kind == 'output':
            return self._exchange(self._stdin, self._stdout, command)
        if kind == 'capture':
            metadata = self._exchange(self._stdin, self._stdout,
                {'command':'capture_binary','count':self.capture_samples,
                 'previewPoints':command.get('previewPoints',1024)})
            if metadata['sequence'] != self._capture_sequence:
                raise ConnectionError('Unexpected capture sequence; reconnect before retrying')
            self._capture_sequence += 1
            size = self.capture_samples*4 + (131072 if metadata['tables'] else 0)
            begin = time.perf_counter(); payload = bytearray()
            while len(payload) < size:
                chunk = self._stdout.read(size-len(payload))
                if not chunk:
                    raise ConnectionError('Truncated binary capture; reconnect before retrying')
                payload.extend(chunk)
            result = self._exchange(self._server.stdin, self._server.stdout,
                {'command':'process_binary','count':self.capture_samples,
                 'maxPoints':command.get('maxPoints',500),
                 'measurementCount':min(self.capture_samples, round(self.measurement_window_ms*125000/self.decimation)),
                 'zero':metadata['zero']}, payload)
            result['preview'] = metadata.get('preview', {'a':[], 'b':[]})
            result['captureTimingMs'] = {key:value for key,value in metadata.items() if key != 'preview'}
            result['captureTimingMs'].update({
                'transferAndServer':(time.perf_counter()-begin)*1000,
                'rawProjection':result['projectionMs'], 'processing':result['processingMs']})
        else:
            result = self._exchange(self._server.stdin, self._server.stdout, command)
        result['backend'] = self.backend
        self._cached = result
        return result

    def close(self):
        if self._socket is not None:
            if self._stdin is not None:
                with suppress(OSError): self._stdin.close()
            if self._stdout is not None:
                with suppress(OSError): self._stdout.close()
            self._socket.close(); self._socket = None
        elif self._stdout is not None:
            with suppress(OSError): self._stdout.channel.shutdown_write()
        if self._client is not None:
            self._client.close()
        if self._server is not None:
            with suppress(OSError): self._server.stdin.close()
            try:
                self._server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._server.terminate(); self._server.wait(timeout=5)
        self._client = self._stdin = self._stdout = self._server = None
        self._cached = {}; self._capture_result = None
        self._capture_sequence = 0
