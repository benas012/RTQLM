import pytest
from pydantic import ValidationError

from redpitaya_acquisition.config.settings import Settings, SourceKind


def test_default_settings_are_simulator_safe() -> None:
    settings = Settings(_env_file=None)
    assert settings.source is SourceKind.SIMULATOR
    assert settings.channel_mask == 3
    assert settings.sample_rate_hz == 125_000_000 / 256


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"channel_1_enabled": False, "channel_2_enabled": False}, "channel"),
        ({"decimation": 0}, "greater than or equal"),
        ({"reconnect_initial_seconds": 2, "reconnect_maximum_seconds": 1}, "maximum"),
        ({"source": "redpitaya", "host": ""}, "host"),
    ],
)
def test_invalid_settings_are_rejected(overrides: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        Settings(_env_file=None, **overrides)
