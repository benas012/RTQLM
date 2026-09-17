import pytest

from redpitaya_acquisition.acquisition.manager import NonRecoverableAcquisitionError
from redpitaya_acquisition.redpitaya.library_loader import load_streaming_library


def test_fake_streaming_module_loads() -> None:
    loaded = load_streaming_library("tests.fakes.fake_rp_stream")
    assert loaded.name == "tests.fakes.fake_rp_stream"
    assert loaded.path


def test_missing_streaming_module_is_actionable() -> None:
    with pytest.raises(NonRecoverableAcquisitionError, match="matching the board OS"):
        load_streaming_library("definitely_missing_redpitaya_module")


def test_installed_windows_client_path_is_discovered(monkeypatch, tmp_path) -> None:
    python_lib = tmp_path / "RedPitaya" / "rpsa-client-2.07-48" / "python_lib"
    python_lib.mkdir(parents=True)
    (python_lib / "streaming.py").write_text(
        "class ADCStreamClient:\n"
        "    pass\n"
        "\n"
        "class ADCCallback:\n"
        "    pass\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.syspath_prepend(str(tmp_path))

    loaded = load_streaming_library()

    assert loaded.name == "streaming"
    assert loaded.path == str(python_lib / "streaming.py")
