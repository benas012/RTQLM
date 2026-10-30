import pytest

from apps.native_receiver.gpio_input import input_readout


@pytest.mark.parametrize("state,level,text", [
    ("watching", 0, "LOW · 0"),
    ("watching", 1, "HIGH · 1"),
    ("watching", None, "—"),
    ("watching", True, "—"),
    ("error", 0, "—"),
    ("disabled", 1, "—"),
])
def test_unknown_or_stale_input_is_not_displayed_as_a_valid_level(state, level, text):
    assert input_readout({"state": state, "level": level})[0] == text
