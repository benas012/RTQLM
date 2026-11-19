import io
import tarfile
from unittest.mock import MagicMock, Mock

import pytest

from quantum_light_engine.devices import native_install
from quantum_light_engine.devices.board_nullifiers import BoardNullifierProcessor


def test_existing_installation_needs_no_sources_upload_or_compiler(monkeypatch):
    client = Mock()
    run = Mock(return_value="PRESENT")
    monkeypatch.setattr(native_install, "_run", run)
    monkeypatch.setattr(native_install, "source_directory", Mock(side_effect=AssertionError))
    assert native_install.ensure_native_worker(client, "/root/worker", "rp-buffered-board") == (
        "present"
    )
    client.open_sftp.assert_not_called()
    run.assert_called_once()


def test_missing_worker_uploads_complete_sources_and_builds_before_publication(monkeypatch):
    client = MagicMock()
    sftp = client.open_sftp.return_value.__enter__.return_value
    uploaded = []
    sftp.putfo.side_effect = lambda data, path: uploaded.append((data.read(), path))
    run = Mock(side_effect=["MISSING", ""])
    monkeypatch.setattr(native_install, "_run", run)
    assert native_install.ensure_native_worker(client, "/root/worker", "rp-buffered-board") == (
        "installed"
    )
    with tarfile.open(fileobj=io.BytesIO(uploaded[0][0]), mode="r:gz") as archive:
        assert set(archive.getnames()) == set(native_install.SOURCES)
        assert archive.extractfile("buffered_worker.c").read().startswith(b"/*")
    build_script = run.call_args.args[1]
    assert "build/rp-buffered-board" in build_script
    assert build_script.index("make -C") < build_script.index("mv -Tf")
    assert "flock -w" in build_script
    assert "BENCHMARKS=/nonexistent" in build_script
    assert "rm -f -- " + uploaded[0][1] in build_script


def test_manual_install_mode_does_not_write_to_the_board(monkeypatch):
    client = Mock()
    monkeypatch.setattr(native_install, "_run", Mock(return_value="MISSING"))
    with pytest.raises(RuntimeError, match="automatic installation is off"):
        native_install.ensure_native_worker(client, "/root/worker", "rp-nullifierd",
                                            auto_install=False)
    client.open_sftp.assert_not_called()


def test_build_failure_is_reported_and_retry_uses_a_fresh_directory(monkeypatch):
    client = MagicMock()
    run = Mock(side_effect=["MISSING", RuntimeError("compiler failed"), "MISSING", ""])
    monkeypatch.setattr(native_install, "_run", run)
    with pytest.raises(RuntimeError, match="compiler failed"):
        native_install.ensure_native_worker(client, "/root/worker", "rp-nullifierd")
    assert native_install.ensure_native_worker(client, "/root/worker", "rp-nullifierd") == (
        "installed"
    )
    first = run.call_args_list[1].args[1]
    retry = run.call_args_list[3].args[1]
    first_directory = first.split("make -C ", 1)[1].split()[0]
    retry_directory = retry.split("make -C ", 1)[1].split()[0]
    assert first_directory != retry_directory


def test_compiler_errors_are_preserved_and_output_is_drained_in_one_stream():
    client = Mock()
    stdout = Mock()
    stdout.read.return_value = b"fatal error: rp.h not found"
    stdout.channel.recv_exit_status.return_value = 2
    client.exec_command.return_value = (Mock(), stdout, Mock())
    with pytest.raises(RuntimeError, match=r"rp\.h not found"):
        native_install._run(client, "make")
    assert "exec 2>&1" in client.exec_command.call_args.args[0]
    stdout.channel.settimeout.assert_called_once_with(300)


@pytest.mark.parametrize("path", ["worker", "/root/../worker", "/"])
def test_installation_rejects_ambiguous_destinations(path):
    client = Mock()
    with pytest.raises(ValueError, match="absolute file path"):
        native_install.ensure_native_worker(client, path, "rp-nullifierd")
    client.exec_command.assert_not_called()


def test_board_selects_the_right_worker_and_exposes_installation_status(monkeypatch):
    installer = Mock(return_value="installed")
    monkeypatch.setattr("quantum_light_engine.devices.board_nullifiers.ensure_native_worker",
                        installer)
    processor = BoardNullifierProcessor("board", "/root/worker", 1, 3, 18902)
    processor.buffered = True
    processor.auto_install = False
    client = Mock()
    processor._ensure_installed(client)
    installer.assert_called_once_with(client, "/root/worker", "rp-buffered-board",
                                      auto_install=False)
    assert processor.status()["processorInstallation"] == "installed"
