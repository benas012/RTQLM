import io
from pathlib import Path
from unittest.mock import Mock

import paramiko
import pytest

from quantum_light_engine.config.settings import EngineSettings
from quantum_light_engine.devices.models import DeviceProfile, DeviceProfileCreate
from quantum_light_engine.devices.session import DeviceSession
from quantum_light_engine.devices.tcp_board_nullifiers import TcpBoardNullifierProcessor


def connected():
    processor = TcpBoardNullifierProcessor("board", "/root/worker", 1, 3, 18902)
    processor._client = Mock()
    processor._socket = Mock()
    processor._stdin = io.StringIO()
    processor._stdout = io.StringIO('{"ok":true,"captures":1}\n')
    return processor


def test_commands_and_results_use_tcp_streams():
    processor = connected()
    assert processor._call({"command": "status"})["captures"] == 1
    assert processor._stdin.getvalue() == '{"command": "status"}\n'
    processor._client.exec_command.assert_not_called()


def test_lost_tcp_connection_closes_socket_and_invalidates_results():
    processor = connected()
    connection = processor._socket
    processor._stdout = io.StringIO("")
    processor._cached = {"calibrated": True}
    with pytest.raises(RuntimeError, match="session closed"):
        processor._call({"command": "capture"})
    connection.shutdown.assert_called_once()
    connection.close.assert_called_once()
    assert processor._socket is processor._client is None
    assert processor._cached == {}
    assert "calibration" in processor._analysis_error


def test_rejected_command_preserves_live_connection():
    processor = connected()
    processor._stdout = io.StringIO('{"ok":false,"error":"invalid setting"}\n')
    with pytest.raises(ValueError, match="invalid setting"):
        processor._call({"command": "configure"})
    processor._socket.close.assert_not_called()
    assert processor._client is not None


def test_close_is_idempotent_and_releases_socket_and_streams():
    processor = connected()
    connection, stdin, stdout = processor._socket, processor._stdin, processor._stdout
    processor.close()
    processor.close()
    connection.close.assert_called_once()
    assert stdin.closed and stdout.closed
    assert processor._socket is processor._client is None


def test_acquisition_requests_only_variances_and_exposes_results():
    processor = connected()
    processor.buffered = True
    processor._call = Mock(return_value={
        "ok":True, "modePeriodSeconds":308e-9, "captures":1,
        "variances":{"cluster_x":[.4]}, "varianceCounts":{"cluster_x":[1]},
        "resultFields":"variances",
        "captureTimingMs":{"taggedE1":True},
    })
    processor.capture_preview()
    processor._call.assert_called_once_with({
        "command":"capture", "maxPoints":500, "resultFields":"variances",
    })
    result = processor.latest()
    assert result['variances'] == {'cluster_x':[.4]}
    assert 'quadratures' not in result and 'preview' not in result
    assert "E1 LOW admitted" in result['status']
    assert "HIGH intervals excluded" in result['status']


def test_buffered_session_configures_tagged_readout():
    profile = DeviceProfile.create(DeviceProfileCreate(
        name="board", source="redpitaya", host="board.local", acquisitionMode="buffered",
    ))
    known_hosts = Path("known_hosts")
    engine = EngineSettings(
        _env_file=None, native_nullifier_enabled=True, buffered_arm_readout="e1_tile64x2",
        buffered_ssh_known_hosts=known_hosts,
    )
    processor = DeviceSession(profile, engine).nullifiers
    assert isinstance(processor, TcpBoardNullifierProcessor)
    assert processor.known_hosts == known_hosts
    assert processor._settings(processor.config)["rawReadout"] == "e1_tile64x2"


def test_disallowed_board_reports_engine_configuration_instead_of_missing_worker():
    profile = DeviceProfile.create(DeviceProfileCreate(
        name="new board", source="redpitaya", host="rp-new.local", acquisitionMode="buffered",
    ))
    engine = EngineSettings(
        _env_file=None, native_nullifier_enabled=True,
        native_nullifier_hosts=["rp-old.local"],
    )
    session = DeviceSession(profile, engine)
    with pytest.raises(RuntimeError, match="QL_NATIVE_NULLIFIER_HOSTS") as exc:
        session._new_source()
    assert "rp-new.local" in str(exc.value)
    assert "installed automatically" in str(exc.value)


def startup(monkeypatch, *, readiness=None, responses=None, connections=None):
    board = TcpBoardNullifierProcessor("board", "/root/worker", 1, 3, 18902)
    board._remote_command = Mock(return_value="v0.94")
    board._ensure_installed = Mock()
    monkeypatch.setattr("quantum_light_engine.devices.tcp_board_nullifiers.sleep", Mock())
    released = []

    def cleanup(_client):
        if board._stale_process is not None:
            released.append(board._stale_process)
            board._stale_process = None

    board._release_stale_process = Mock(side_effect=cleanup)
    clients, sockets = [], []
    for index, line in enumerate(readiness or ["READY\n"]):
        client = Mock()
        client.get_transport.return_value.getpeername.return_value = ("192.0.2.1", 22)
        stdout = io.StringIO(f"RTQLM_SESSION {2000 + index} {6000 + index}\n")
        stdout.channel = Mock()
        client.exec_command.return_value = (Mock(), stdout, io.StringIO(line))
        clients.append(client)
        connection = Mock()
        reply = (responses or ['{"ok":true}\n'])[min(index, len(responses or [0]) - 1)]
        connection.makefile.side_effect = [io.StringIO(), io.StringIO(reply)]
        sockets.append(connection)
    factory = Mock(side_effect=clients)
    monkeypatch.setattr(
        "quantum_light_engine.devices.tcp_board_nullifiers.paramiko.SSHClient", factory,
    )
    connector = Mock(side_effect=connections or sockets)
    monkeypatch.setattr(
        "quantum_light_engine.devices.tcp_board_nullifiers.socket.create_connection", connector,
    )
    return board, clients, sockets, released, factory, connector


def test_tcp_startup_tracks_identity_cleans_stale_worker_and_closes_its_own_process(monkeypatch):
    board, clients, sockets, released, _, _ = startup(monkeypatch)
    board._stale_process = (1234, 5678)
    board.prepare_capture()
    assert released == [(1234, 5678)]
    assert board._remote_process == (2000, 6000)
    clients[0].get_transport.return_value.set_keepalive.assert_called_once_with(5)
    launch = clients[0].exec_command.call_args.args[0]
    assert "RTQLM_SESSION" in launch and "exec /root/worker --listen 18903" in launch
    board.close()
    assert released == [(1234, 5678), (2000, 6000)]
    sockets[0].shutdown.assert_called_once()
    assert board._remote_process is board._stale_process is None


def test_tcp_startup_retries_failed_handshake_after_releasing_only_its_worker(monkeypatch):
    board, clients, _, released, factory, connector = startup(
        monkeypatch, readiness=["", "READY\n"],
    )
    board.prepare_capture()
    assert factory.call_count == 2 and connector.call_count == 1
    assert released == [(2000, 6000)]
    assert board._remote_process == (2001, 6001)
    clients[0].close.assert_called_once()
    board.close()


def test_tcp_startup_retries_socket_failure_without_leaving_a_listener(monkeypatch):
    board, clients, sockets, released, factory, connector = startup(
        monkeypatch, readiness=["READY\n", "READY\n"],
    )
    connector.side_effect = [TimeoutError("TCP handshake timed out"), sockets[1]]
    board.prepare_capture()
    assert factory.call_count == connector.call_count == 2
    assert released == [(2000, 6000)]
    clients[0].close.assert_called_once()
    assert board._remote_process == (2001, 6001)
    board.close()


def test_tcp_startup_exposes_listener_failure_after_bounded_retry(monkeypatch):
    board, _, _, released, factory, connector = startup(
        monkeypatch, readiness=["ERROR: TCP port 18903: Address already in use\n"] * 2,
    )
    with pytest.raises(RuntimeError, match=r"port 18903.*Address already in use"):
        board.prepare_capture()
    assert factory.call_count == 2
    connector.assert_not_called()
    assert released == [(2000, 6000), (2001, 6001)]


def test_tcp_startup_waits_for_old_listener_to_expire_before_retry(monkeypatch):
    import quantum_light_engine.devices.tcp_board_nullifiers as tcp_module

    board, _, _, _, factory, _ = startup(
        monkeypatch, readiness=["ERROR: TCP port 18903: Address already in use\n", "READY\n"],
    )
    board.prepare_capture()
    tcp_module.sleep.assert_called_once_with(15.1)
    assert factory.call_count == 2
    board.close()


def test_tcp_startup_diagnostics_redact_the_session_token(monkeypatch):
    token = "a" * 64
    monkeypatch.setattr("quantum_light_engine.devices.tcp_board_nullifiers.secrets.token_hex",
                        Mock(return_value=token))
    board, _, _, _, _, _ = startup(monkeypatch, readiness=["ERROR: " + token + "\n"] * 2)
    with pytest.raises(RuntimeError) as failure:
        board.prepare_capture()
    assert token not in str(failure.value)
    assert "[redacted]" in str(failure.value)


def test_tcp_startup_does_not_retry_rejected_configuration(monkeypatch):
    board, _, _, released, factory, _ = startup(
        monkeypatch, responses=['{"ok":false,"error":"invalid setting"}\n'],
    )
    with pytest.raises(ValueError, match="invalid setting"):
        board.prepare_capture()
    assert factory.call_count == 1
    assert released == [(2000, 6000)]


def test_tcp_authentication_failure_is_not_retried(monkeypatch):
    board, clients, _, _, factory, connector = startup(monkeypatch)
    clients[0].connect.side_effect = paramiko.AuthenticationException("authentication rejected")
    with pytest.raises(paramiko.AuthenticationException):
        board.prepare_capture()
    assert factory.call_count == 1
    connector.assert_not_called()


def test_tcp_cleanup_failure_keeps_process_identity_for_later_reconnect(monkeypatch):
    board, _, _, _, _, _ = startup(monkeypatch)
    board.prepare_capture()
    board._release_stale_process.side_effect = RuntimeError("SSH closed")
    board.close()
    assert board._stale_process == (2000, 6000)
    assert board._remote_process is board._client is None
