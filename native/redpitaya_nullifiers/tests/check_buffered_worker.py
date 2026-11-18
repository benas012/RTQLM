"""Exercise the actual binary worker framing and exact-window projection."""
import json
import os
import socket
import struct
import subprocess
import sys
import time
import unittest

BINARY = sys.argv.pop(1)


class BinaryWorker(unittest.TestCase):
    def listener_port(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        return listener, listener.getsockname()[1]

    def test_listener_reports_occupied_port(self):
        listener, port = self.listener_port()
        with listener:
            listener.listen(1)
            result = subprocess.run([BINARY, "--listen", str(port)], capture_output=True,
                                    timeout=3, env={**os.environ, "NF_BUFFER_TOKEN": "a" * 64})
        self.assertEqual(result.returncode, 2)
        self.assertIn(f"TCP port {port}".encode(), result.stderr)
        self.assertNotIn(b"READY", result.stderr)

    def test_abandoned_listener_releases_port_after_startup_timeout(self):
        listener, port = self.listener_port()
        listener.close()
        with subprocess.Popen([BINARY, "--listen", str(port)], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE,
                              env={**os.environ, "NF_BUFFER_TOKEN": "a" * 64}) as process:
            try:
                self.assertEqual(process.stderr.readline(), b"READY\n")
                self.assertEqual(process.wait(timeout=18), 2)
                self.assertIn(b"did not connect within 15 seconds", process.stderr.read())
                with socket.socket() as probe:
                    probe.bind(("127.0.0.1", port))
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=3)

    def test_authenticated_tcp_worker_survives_idle_and_exits_after_disconnect(self):
        listener, port = self.listener_port()
        listener.close()
        with subprocess.Popen([BINARY, "--listen", str(port)], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE,
                              env={**os.environ, "NF_BUFFER_TOKEN": "a" * 64}) as process:
            try:
                self.assertEqual(process.stderr.readline(), b"READY\n")
                with socket.create_connection(("127.0.0.1", port), timeout=3) as connection:
                    connection.sendall(b"a" * 64 + b'\n{"command":"status"}\n')
                    with connection.makefile("r") as stream:
                        first = json.loads(stream.readline())
                        self.assertTrue(first["ok"])
                        time.sleep(17)  # Longer than the 15-second authentication deadline.
                        self.assertIsNone(process.poll(), "authenticated idle worker exited")
                        connection.sendall(b'{"command":"status"}\n')
                        again = json.loads(stream.readline())
                        self.assertTrue(again["ok"])
                        self.assertEqual(again["revision"], first["revision"])
                self.assertEqual(process.wait(timeout=3), 0)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=3)

    def invoke(self, tail, updates=None):
        config = {'command':'configure','config':{
            'decimation':2,'captureSamples':312512,'modeShape':'gaussian_linear',
            'modeWidthSeconds':308e-9,'collectionGuardSeconds':0,
            'collectionEndGuardSamples':0,'channelBDelaySamples':0,
            'acquisitionGatePin':None}}
        config['config'].update(updates or {})
        data = json.dumps(config).encode()+b'\n'+tail
        result = subprocess.run([BINARY],input=data,capture_output=True,timeout=10,check=True)
        return [json.loads(line) for line in result.stdout.splitlines()]

    def test_padding_is_transferred_but_excluded_from_processing(self):
        command = {'command':'process_binary','count':312512,'measurementCount':312500,'zero':[0,0]}
        # Only the padded tail contains nonzero samples. No integrated mode may see it.
        rail = bytes(312500*2)+struct.pack('<12h',*([8191]*12))
        replies = self.invoke(json.dumps(command).encode()+b'\n'+rail+rail)
        self.assertTrue(replies[-1]['ok'])
        self.assertEqual(replies[-1]['modeSamples'],19)
        self.assertEqual(replies[-1]['modeSamplesMax'],20)
        self.assertTrue(all(v==0 for v in replies[-1]['quadratures']['a']))

    def test_truncation_fails_closed(self):
        command = {'command':'process_binary','count':312512,'zero':[0,0]}
        replies = self.invoke(json.dumps(command).encode()+b'\n'+bytes(20))
        self.assertFalse(replies[-1]['ok'])
        self.assertIn('truncated',replies[-1]['error'])

    def test_oversized_count_rejected_before_payload_read(self):
        command = {'command':'process_binary','count':4294967295,'zero':[0,0]}
        replies = self.invoke(json.dumps(command).encode()+b'\n')
        self.assertFalse(replies[-1]['ok'])

    def test_complete_50ms_record_is_processed_beyond_old_history_limit(self):
        count, measurement = 6250048, 6250000
        command = {'command':'process_binary', 'count':count,
                   'measurementCount':measurement, 'zero':[0,0], 'maxPoints':262144}
        # The complete last temporal bin carries signal; aligned padding must be ignored.
        rail = bytes(6249936*2) + struct.pack('<112h', *([1]*10+[0]*54+[8191]*48))
        replies = self.invoke(json.dumps(command).encode()+b'\n'+rail+rail,
                              {'decimation':1, 'captureSamples':count,
                               'measurementSamples':measurement,
                               'historyModes':200000})
        result = replies[-1]
        self.assertTrue(result['ok'], result)
        self.assertFalse(result['prefixTruncated'])
        self.assertEqual(len(result['quadratures']['a']), 162337)
        self.assertNotEqual(result['quadratures']['a'][-1], 0)

    def test_capture_count_must_match_allocated_buffer(self):
        replies = self.invoke(b'{"command":"capture_binary","count":64}\n')
        self.assertFalse(replies[-1]['ok'])
        self.assertIn('must equal captureSamples', replies[-1]['error'])


if __name__ == '__main__':
    unittest.main()
