from __future__ import annotations

from pathlib import Path

from pytest import MonkeyPatch

from apps.native_receiver import settings


def test_receiver_settings_round_trip(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    path = tmp_path / "receiver.json"
    monkeypatch.setattr(settings, "SETTINGS_PATH", path)
    expected = settings.AppSettings("http://engine.local:9000", False)

    settings.save_settings(expected)

    assert settings.load_settings() == expected
    assert not path.with_suffix(".tmp").exists()


def test_invalid_receiver_settings_fall_back_to_defaults(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    path = tmp_path / "receiver.json"
    path.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(settings, "SETTINGS_PATH", path)

    assert settings.load_settings() == settings.AppSettings()
