from dataclasses import replace

import numpy as np
import pytest

from apps.native_receiver import settings
from quantum_light_engine.devices.board_nullifiers import BoardNullifierProcessor
from quantum_light_engine.mode_expression import (
    MODE_EXPRESSIONS,
    evaluate_mode_expression,
    parse_mode_expression,
)
from quantum_light_engine.post_processing import (
    RealtimeClusterProcessor,
    config_from_dict,
    config_to_dict,
    expression_mode_templates,
    lab_processing_config,
)


@pytest.mark.parametrize("shape", list(MODE_EXPRESSIONS))
@pytest.mark.parametrize("width", [256e-9, 308e-9])
def test_editable_builtin_formulas_reproduce_existing_projection(shape, width):
    config = replace(lab_processing_config(), mode_shape=shape, mode_width_seconds=width,
                     window_mode="continuous", variance_estimator="rolling",
                     collection_guard_seconds=0, collection_end_guard_samples=0)
    custom = replace(config, mode_expression=MODE_EXPRESSIONS[shape])
    rng = np.random.default_rng(832)
    a, b = rng.normal(size=(2, 4096))
    expected = RealtimeClusterProcessor(config).feed(a, b, 125e6)
    actual = RealtimeClusterProcessor(custom).feed(a, b, 125e6)
    assert actual.mode_samples == expected.mode_samples
    assert actual.mode_samples_max == expected.mode_samples_max
    for channel in ("a", "b"):
        np.testing.assert_allclose(actual.quadratures[channel], expected.quadratures[channel],
                                   rtol=1e-11, atol=1e-17)


def test_numpy_notation_constants_and_piecewise_windows():
    time = np.array([-0.2, 0, 0.2])
    actual = evaluate_mode_expression(
        "np.where(abs(t) < T/4, np.cos(2*np.pi*f*t), 0)", time, 0.4, 2,
    )
    np.testing.assert_array_equal(actual, [0, 1, 0])
    np.testing.assert_array_equal(evaluate_mode_expression("2", time, 1, 1), [2, 2, 2])


@pytest.mark.parametrize("expression", [
    "__import__('os').system('echo bad')", "np.load('file')", "t.__class__", "[t]",
    "t[0]", "lambda t: t", "[x for x in t]", "open('file')", "unknown + t",
        "t; 1", "1e999", "np.t", "exp(t, out=t)", "True", "", "t+", "1" * 2049, "9" * 2000,
])
def test_expressions_reject_python_code_and_invalid_syntax(expression):
    with pytest.raises(ValueError, match=r"[Mm]ode function"):
        parse_mode_expression(expression)


@pytest.mark.parametrize("expression", ["0", "t-t", "sqrt(-1)", "1 / t", "exp(1e100)"])
def test_invalid_sampled_weights_are_rejected(expression):
    # A lab fine grid includes its exact center, so 1/t is rejected too.
    config = replace(lab_processing_config(), mode_expression=expression)
    with pytest.raises(ValueError, match=r"finite|norm"):
        expression_mode_templates(config, 125e6)


def test_custom_function_changes_weights_is_saved_and_reaches_native_backend(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CLUSTER_CONFIG_PATH", tmp_path / "cluster.json")
    config = replace(lab_processing_config(), mode_expression="exp(-0.5*(t/(T/5))**2)")
    assert config_from_dict(config_to_dict(config)) == config
    settings.save_cluster_config(config)
    assert settings.load_cluster_config() == config
    step, templates = expression_mode_templates(config, 125e6)
    assert step == 38.5
    assert set(templates) == {38, 39}
    for mode in templates.values():
        assert np.sum(mode**2) / 125e6 == pytest.approx(1)
        assert np.all(mode > 0)
    board = BoardNullifierProcessor("board.local", "/root/processor", 1, 3, 18902)
    payload = board._settings(config)
    assert "modeExpression" not in payload
    for actual, size in zip(payload["sampledModeTemplates"], sorted(templates), strict=True):
        np.testing.assert_array_equal(actual, templates[size])
    board.config = config
    assert board._settings(lab_processing_config())["sampledModeTemplates"] == []


def test_expression_and_explicit_template_cannot_silently_override_each_other():
    with pytest.raises(ValueError, match="not both"):
        replace(lab_processing_config(), mode_expression="1", explicit_mode_template=(1, 2))
