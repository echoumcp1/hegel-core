"""Tests for conformance.py."""

import os
import stat
import sys
import tempfile

import pytest
from hypothesis import given, settings, strategies as st

from hegel.conformance import (
    BinaryConformance,
    BooleanConformance,
    ConformanceTest,
    DictConformance,
    FloatConformance,
    IntegerConformance,
    ListConformance,
    SampledFromConformance,
    TextConformance,
    _integer_params_strategy,
)


@given(_integer_params_strategy(None, None))
@settings(max_examples=10)
def test_integer_params_strategy_no_bounds(params):
    """Test _integer_params_strategy with no bounds."""
    assert "min_value" in params
    assert "max_value" in params


@given(_integer_params_strategy(0, 100))
@settings(max_examples=10)
def test_integer_params_strategy_with_bounds(params):
    """Test _integer_params_strategy with bounds."""
    if params["min_value"] is not None:
        assert params["min_value"] >= 0
    if params["max_value"] is not None:
        assert params["max_value"] <= 100
    if params["min_value"] is not None and params["max_value"] is not None:
        assert params["min_value"] <= params["max_value"]


@given(_integer_params_strategy(-10, None))
@settings(max_examples=10)
def test_integer_params_strategy_min_only(params):
    """Test _integer_params_strategy with only min bound."""
    if params["min_value"] is not None:
        assert params["min_value"] >= -10


@given(_integer_params_strategy(None, 50))
@settings(max_examples=10)
def test_integer_params_strategy_max_only(params):
    """Test _integer_params_strategy with only max bound."""
    if params["max_value"] is not None:
        assert params["max_value"] <= 50


def _make_conformance_binary(script_body):
    """Create a temporary executable Python script for conformance testing."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, prefix="conform_",
    ) as f:
        f.write(f"#!{sys.executable}\n")
        f.write("import json, os, sys\n")
        f.write("params = json.loads(sys.argv[1])\n")
        f.write("metrics_file = os.environ['CONFORMANCE_METRICS_FILE']\n")
        f.write("test_cases = int(os.environ['CONFORMANCE_TEST_CASES'])\n")
        f.write("with open(metrics_file, 'w') as mf:\n")
        f.write(f"    {script_body}\n")
    f.close()
    os.chmod(f.name, os.stat(f.name).st_mode | stat.S_IEXEC)
    return f.name


# --- BooleanConformance ---


def test_boolean_conformance_params_strategy():
    """Test BooleanConformance generates empty params."""
    binary_path = _make_conformance_binary(
        "mf.write(json.dumps({'value': True}) + '\\n')",
    )
    try:
        bc = BooleanConformance(binary_path, test_cases=1)
        # params_strategy returns st.just({})
        result = bc.params_strategy()
        assert result is not None
    finally:
        os.unlink(binary_path)


def test_boolean_conformance_validate():
    """Test BooleanConformance validate."""
    binary_path = _make_conformance_binary(
        "mf.write(json.dumps({'value': True}) + '\\n')",
    )
    try:
        bc = BooleanConformance(binary_path, test_cases=1)
        bc.validate([{"value": True}, {"value": False}], {})
    finally:
        os.unlink(binary_path)


def test_boolean_conformance_validate_fails():
    """Test BooleanConformance validate fails on bad data."""
    binary_path = _make_conformance_binary(
        "mf.write(json.dumps({'value': True}) + '\\n')",
    )
    try:
        bc = BooleanConformance(binary_path, test_cases=1)
        with pytest.raises(AssertionError):
            bc.validate([{"value": 42}], {})
    finally:
        os.unlink(binary_path)


def test_boolean_conformance_run():
    """Test BooleanConformance.run()."""
    binary_path = _make_conformance_binary(
        "mf.write(json.dumps({'value': True}) + '\\n')",
    )
    try:
        bc = BooleanConformance(binary_path, test_cases=1)
        bc.run({})
    finally:
        os.unlink(binary_path)


def test_conformance_run_failure():
    """Test ConformanceTest.run() raises on non-zero exit code."""
    binary_path = _make_conformance_binary("sys.exit(1)")
    try:
        bc = BooleanConformance(binary_path, test_cases=1)
        with pytest.raises(RuntimeError, match="exit code"):
            bc.run({})
    finally:
        os.unlink(binary_path)


# --- IntegerConformance ---


def test_integer_conformance_params_strategy():
    """Test IntegerConformance generates valid params."""
    binary_path = _make_conformance_binary(
        "mf.write(json.dumps({'value': 5}) + '\\n')",
    )
    try:
        ic = IntegerConformance(binary_path, test_cases=1, min_value=0, max_value=100)
        result = ic.params_strategy()
        assert result is not None
    finally:
        os.unlink(binary_path)


def test_integer_conformance_validate():
    """Test IntegerConformance validate with bounds."""
    binary_path = _make_conformance_binary(
        "mf.write(json.dumps({'value': 5}) + '\\n')",
    )
    try:
        ic = IntegerConformance(binary_path, test_cases=1)
        ic.validate(
            [{"value": 5}, {"value": 10}],
            {"min_value": 0, "max_value": 100},
        )
    finally:
        os.unlink(binary_path)


def test_integer_conformance_validate_no_bounds():
    """Test IntegerConformance validate without bounds."""
    binary_path = _make_conformance_binary(
        "mf.write(json.dumps({'value': 5}) + '\\n')",
    )
    try:
        ic = IntegerConformance(binary_path, test_cases=1)
        ic.validate(
            [{"value": 5}],
            {"min_value": None, "max_value": None},
        )
    finally:
        os.unlink(binary_path)


def test_integer_conformance_validate_fails_min():
    """Test IntegerConformance validate fails when below min."""
    binary_path = _make_conformance_binary(
        "mf.write(json.dumps({'value': 5}) + '\\n')",
    )
    try:
        ic = IntegerConformance(binary_path, test_cases=1)
        with pytest.raises(AssertionError):
            ic.validate([{"value": -1}], {"min_value": 0, "max_value": 100})
    finally:
        os.unlink(binary_path)


def test_integer_conformance_validate_fails_max():
    """Test IntegerConformance validate fails when above max."""
    binary_path = _make_conformance_binary(
        "mf.write(json.dumps({'value': 5}) + '\\n')",
    )
    try:
        ic = IntegerConformance(binary_path, test_cases=1)
        with pytest.raises(AssertionError):
            ic.validate([{"value": 101}], {"min_value": 0, "max_value": 100})
    finally:
        os.unlink(binary_path)


def test_integer_conformance_run():
    """Test IntegerConformance run with output."""
    script = (
        "for i in range(test_cases):\n"
        "        mf.write(json.dumps({'value': 5}) + '\\n')"
    )
    binary_path = _make_conformance_binary(script)
    try:
        ic = IntegerConformance(binary_path, test_cases=2, min_value=0, max_value=10)
        ic.run({"min_value": 0, "max_value": 10})
    finally:
        os.unlink(binary_path)


# --- FloatConformance ---


def test_float_conformance_params_strategy():
    """Test FloatConformance generates valid params."""
    binary_path = _make_conformance_binary(
        "mf.write(json.dumps({'value': 1.5}) + '\\n')",
    )
    try:
        fc = FloatConformance(binary_path, test_cases=1)
        result = fc.params_strategy()
        assert result is not None
    finally:
        os.unlink(binary_path)


def test_float_conformance_params_min_equals_max():
    """Test FloatConformance strategy when min == max resets exclude flags."""
    binary_path = _make_conformance_binary(
        "mf.write(json.dumps({'value': 1.0}) + '\\n')",
    )
    try:
        fc = FloatConformance(binary_path, test_cases=1)

        # Draw from the strategy many times until we hit the min==max path
        from hypothesis import find

        params = find(
            fc.params_strategy(),
            lambda p: (
                p["min_value"] is not None
                and p["max_value"] is not None
                and p["min_value"] == p["max_value"]
            ),
        )
        # When min==max, excludes must be False
        assert params["exclude_min"] is False
        assert params["exclude_max"] is False
    finally:
        os.unlink(binary_path)


def test_float_conformance_validate():
    """Test FloatConformance validate with bounds."""
    binary_path = _make_conformance_binary(
        "mf.write(json.dumps({'value': 1.5}) + '\\n')",
    )
    try:
        fc = FloatConformance(binary_path, test_cases=1)
        fc.validate(
            [{"value": 1.5}, {"value": 2.0}],
            {
                "min_value": 0.0,
                "max_value": 10.0,
                "exclude_min": False,
                "exclude_max": False,
                "allow_nan": False,
                "allow_infinity": False,
            },
        )
    finally:
        os.unlink(binary_path)


def test_float_conformance_validate_nan():
    """Test FloatConformance validate with NaN."""
    binary_path = _make_conformance_binary(
        "mf.write(json.dumps({'value': 1.5}) + '\\n')",
    )
    try:
        fc = FloatConformance(binary_path, test_cases=1)
        fc.validate(
            [{"value": 1.5, "is_nan": True}],
            {
                "min_value": None,
                "max_value": None,
                "exclude_min": False,
                "exclude_max": False,
                "allow_nan": True,
                "allow_infinity": False,
            },
        )
    finally:
        os.unlink(binary_path)


def test_float_conformance_validate_infinity():
    """Test FloatConformance validate with infinity."""
    binary_path = _make_conformance_binary(
        "mf.write(json.dumps({'value': 1.5}) + '\\n')",
    )
    try:
        fc = FloatConformance(binary_path, test_cases=1)
        fc.validate(
            [{"value": 1.5, "is_infinite": True}],
            {
                "min_value": None,
                "max_value": None,
                "exclude_min": False,
                "exclude_max": False,
                "allow_nan": False,
                "allow_infinity": True,
            },
        )
    finally:
        os.unlink(binary_path)


def test_float_conformance_validate_no_bounds():
    """Test FloatConformance validate with no bounds (min/max are None)."""
    binary_path = _make_conformance_binary(
        "mf.write(json.dumps({'value': 1.5}) + '\\n')",
    )
    try:
        fc = FloatConformance(binary_path, test_cases=1)
        fc.validate(
            [{"value": 1.5}, {"value": -999.0}],
            {
                "min_value": None,
                "max_value": None,
                "exclude_min": False,
                "exclude_max": False,
                "allow_nan": False,
                "allow_infinity": False,
            },
        )
    finally:
        os.unlink(binary_path)


def test_float_conformance_validate_exclude_min():
    """Test FloatConformance validate with exclude_min."""
    binary_path = _make_conformance_binary(
        "mf.write(json.dumps({'value': 1.5}) + '\\n')",
    )
    try:
        fc = FloatConformance(binary_path, test_cases=1)
        with pytest.raises(AssertionError):
            fc.validate(
                [{"value": 0.0}],
                {
                    "min_value": 0.0,
                    "max_value": 10.0,
                    "exclude_min": True,
                    "exclude_max": False,
                    "allow_nan": False,
                    "allow_infinity": False,
                },
            )
    finally:
        os.unlink(binary_path)


def test_float_conformance_validate_exclude_max():
    """Test FloatConformance validate with exclude_max."""
    binary_path = _make_conformance_binary(
        "mf.write(json.dumps({'value': 1.5}) + '\\n')",
    )
    try:
        fc = FloatConformance(binary_path, test_cases=1)
        with pytest.raises(AssertionError):
            fc.validate(
                [{"value": 10.0}],
                {
                    "min_value": 0.0,
                    "max_value": 10.0,
                    "exclude_min": False,
                    "exclude_max": True,
                    "allow_nan": False,
                    "allow_infinity": False,
                },
            )
    finally:
        os.unlink(binary_path)


# --- TextConformance ---


def test_text_conformance_params_strategy():
    """Test TextConformance generates valid params."""
    binary_path = _make_conformance_binary(
        "mf.write(json.dumps({'length': 5}) + '\\n')",
    )
    try:
        tc = TextConformance(binary_path, test_cases=1)
        result = tc.params_strategy()
        assert result is not None
    finally:
        os.unlink(binary_path)


def test_text_conformance_validate():
    """Test TextConformance validate."""
    binary_path = _make_conformance_binary(
        "mf.write(json.dumps({'length': 5}) + '\\n')",
    )
    try:
        tc = TextConformance(binary_path, test_cases=1)
        tc.validate(
            [{"length": 5}, {"length": 10}],
            {"min_size": 0, "max_size": 20},
        )
    finally:
        os.unlink(binary_path)


def test_text_conformance_validate_no_max():
    """Test TextConformance validate without max_size."""
    binary_path = _make_conformance_binary(
        "mf.write(json.dumps({'length': 5}) + '\\n')",
    )
    try:
        tc = TextConformance(binary_path, test_cases=1)
        tc.validate(
            [{"length": 5}],
            {"min_size": 0, "max_size": None},
        )
    finally:
        os.unlink(binary_path)


def test_text_conformance_validate_fails_min():
    """Test TextConformance validate fails below min."""
    binary_path = _make_conformance_binary(
        "mf.write(json.dumps({'length': 5}) + '\\n')",
    )
    try:
        tc = TextConformance(binary_path, test_cases=1)
        with pytest.raises(AssertionError):
            tc.validate([{"length": 1}], {"min_size": 5, "max_size": None})
    finally:
        os.unlink(binary_path)


# --- BinaryConformance ---


def test_binary_conformance_validate():
    """Test BinaryConformance validate."""
    binary_path = _make_conformance_binary(
        "mf.write(json.dumps({'length': 5}) + '\\n')",
    )
    try:
        bc = BinaryConformance(binary_path, test_cases=1)
        bc.validate(
            [{"length": 5}],
            {"min_size": 0, "max_size": 10},
        )
    finally:
        os.unlink(binary_path)


def test_binary_conformance_validate_no_max():
    """Test BinaryConformance validate without max."""
    binary_path = _make_conformance_binary(
        "mf.write(json.dumps({'length': 5}) + '\\n')",
    )
    try:
        bc = BinaryConformance(binary_path, test_cases=1)
        bc.validate([{"length": 5}], {"min_size": 0, "max_size": None})
    finally:
        os.unlink(binary_path)


def test_binary_conformance_params_strategy():
    """Test BinaryConformance generates valid params."""
    binary_path = _make_conformance_binary(
        "mf.write(json.dumps({'length': 5}) + '\\n')",
    )
    try:
        bc = BinaryConformance(binary_path, test_cases=1)
        result = bc.params_strategy()
        assert result is not None
    finally:
        os.unlink(binary_path)


# --- ListConformance ---


def test_list_conformance_params_strategy():
    """Test ListConformance generates valid params."""
    binary_path = _make_conformance_binary(
        "mf.write(json.dumps({'size': 1, 'min_element': 5, 'max_element': 5}) + '\\n')",
    )
    try:
        lc = ListConformance(binary_path, test_cases=1, min_value=0, max_value=100)
        result = lc.params_strategy()
        assert result is not None
    finally:
        os.unlink(binary_path)


def test_list_conformance_validate():
    """Test ListConformance validate."""
    binary_path = _make_conformance_binary(
        "mf.write(json.dumps({'size': 1, 'min_element': 5, 'max_element': 5}) + '\\n')",
    )
    try:
        lc = ListConformance(binary_path, test_cases=1)
        lc.validate(
            [{"size": 2, "min_element": 3, "max_element": 7}],
            {"min_size": 0, "max_size": 10, "min_value": 0, "max_value": 100},
        )
    finally:
        os.unlink(binary_path)


def test_list_conformance_validate_empty():
    """Test ListConformance validate with empty list."""
    binary_path = _make_conformance_binary(
        "mf.write(json.dumps({'size': 0}) + '\\n')",
    )
    try:
        lc = ListConformance(binary_path, test_cases=1)
        lc.validate(
            [{"size": 0}],
            {"min_size": 0, "max_size": 10, "min_value": None, "max_value": None},
        )
    finally:
        os.unlink(binary_path)


def test_list_conformance_validate_no_max_size():
    """Test ListConformance validate without max_size."""
    binary_path = _make_conformance_binary(
        "mf.write(json.dumps({'size': 1, 'min_element': 5, 'max_element': 5}) + '\\n')",
    )
    try:
        lc = ListConformance(binary_path, test_cases=1)
        lc.validate(
            [{"size": 2, "min_element": 3, "max_element": 7}],
            {"min_size": 0, "max_size": None, "min_value": None, "max_value": None},
        )
    finally:
        os.unlink(binary_path)


# --- SampledFromConformance ---


def test_sampled_from_conformance_validate():
    """Test SampledFromConformance validate."""
    binary_path = _make_conformance_binary(
        "mf.write(json.dumps({'value': 5}) + '\\n')",
    )
    try:
        sc = SampledFromConformance(binary_path, test_cases=1)
        sc.validate([{"value": 5}], {"options": [1, 5, 10]})
    finally:
        os.unlink(binary_path)


def test_sampled_from_conformance_params_strategy():
    """Test SampledFromConformance generates valid params."""
    binary_path = _make_conformance_binary(
        "mf.write(json.dumps({'value': 5}) + '\\n')",
    )
    try:
        sc = SampledFromConformance(binary_path, test_cases=1)
        result = sc.params_strategy()
        assert result is not None
    finally:
        os.unlink(binary_path)


# --- DictConformance ---


def test_dict_conformance_params_strategy():
    """Test DictConformance generates valid params."""
    binary_path = _make_conformance_binary(
        "mf.write(json.dumps({"
        "'size': 1, 'min_key': 1, 'max_key': 1, "
        "'min_value': 5, 'max_value': 5}) + '\\n')",
    )
    try:
        dc = DictConformance(binary_path, test_cases=1)
        result = dc.params_strategy()
        assert result is not None
    finally:
        os.unlink(binary_path)


def test_dict_conformance_validate():
    """Test DictConformance validate."""
    binary_path = _make_conformance_binary(
        "mf.write(json.dumps({"
        "'size': 1, 'min_key': 1, 'max_key': 1, "
        "'min_value': 5, 'max_value': 5}) + '\\n')",
    )
    try:
        dc = DictConformance(binary_path, test_cases=1)
        dc.validate(
            [
                {
                    "size": 2,
                    "min_key": 1,
                    "max_key": 3,
                    "min_value": 5,
                    "max_value": 10,
                },
            ],
            {
                "min_size": 0,
                "max_size": 10,
                "key_type": "integer",
                "min_key": 0,
                "max_key": 100,
                "min_value": 0,
                "max_value": 100,
            },
        )
    finally:
        os.unlink(binary_path)


def test_dict_conformance_validate_string_keys():
    """Test DictConformance validate with string keys."""
    binary_path = _make_conformance_binary(
        "mf.write(json.dumps({'size': 1, 'min_value': 5, 'max_value': 5}) + '\\n')",
    )
    try:
        dc = DictConformance(binary_path, test_cases=1)
        dc.validate(
            [{"size": 1, "min_value": 5, "max_value": 10}],
            {
                "min_size": 0,
                "max_size": 10,
                "key_type": "string",
                "min_key": 0,
                "max_key": 100,
                "min_value": 0,
                "max_value": 100,
            },
        )
    finally:
        os.unlink(binary_path)


def test_dict_conformance_validate_empty():
    """Test DictConformance validate with empty dict."""
    binary_path = _make_conformance_binary(
        "mf.write(json.dumps({'size': 0}) + '\\n')",
    )
    try:
        dc = DictConformance(binary_path, test_cases=1)
        dc.validate(
            [{"size": 0}],
            {
                "min_size": 0,
                "max_size": 10,
                "key_type": "integer",
                "min_key": 0,
                "max_key": 100,
                "min_value": 0,
                "max_value": 100,
            },
        )
    finally:
        os.unlink(binary_path)


def test_dict_conformance_custom_bounds():
    """Test DictConformance with custom key/value bounds."""
    binary_path = _make_conformance_binary(
        "mf.write(json.dumps({'size': 0}) + '\\n')",
    )
    try:
        dc = DictConformance(
            binary_path,
            test_cases=1,
            min_key=-10,
            max_key=10,
            min_value=-5,
            max_value=5,
        )
        assert dc.min_key == -10
        assert dc.max_key == 10
        assert dc.min_value == -5
        assert dc.max_value == 5
    finally:
        os.unlink(binary_path)


# --- ConformanceTest base class ---


def test_conformance_test_default_test_cases():
    """Test ConformanceTest uses default_test_cases when none specified."""
    binary_path = _make_conformance_binary(
        "mf.write(json.dumps({'value': True}) + '\\n')",
    )
    try:
        bc = BooleanConformance(binary_path)
        assert bc.test_cases == BooleanConformance.default_test_cases
    finally:
        os.unlink(binary_path)


def test_conformance_test_nonexistent_binary():
    """Test ConformanceTest asserts binary exists."""
    with pytest.raises(AssertionError):
        BooleanConformance("/nonexistent/path/to/binary")


def test_conformance_registered_tests():
    """Test that all conformance test classes are registered."""
    expected = {
        BooleanConformance,
        IntegerConformance,
        FloatConformance,
        TextConformance,
        BinaryConformance,
        ListConformance,
        SampledFromConformance,
        DictConformance,
    }
    assert expected == ConformanceTest.registered_tests


def test_float_conformance_default_test_cases():
    """Test FloatConformance has higher default_test_cases."""
    assert FloatConformance.default_test_cases == 500


# --- Test strategy drawing ---


@given(st.data())
@settings(max_examples=5)
def test_float_strategy_draws(data):
    """Test FloatConformance.params_strategy() produces valid params."""
    binary_path = _make_conformance_binary(
        "mf.write(json.dumps({'value': 1.0}) + '\\n')",
    )
    try:
        fc = FloatConformance(binary_path, test_cases=1)
        params = data.draw(fc.params_strategy())
        assert "min_value" in params
        assert "max_value" in params
        assert "exclude_min" in params
        assert "exclude_max" in params
        assert "allow_nan" in params
        assert "allow_infinity" in params
    finally:
        os.unlink(binary_path)


@given(st.data())
@settings(max_examples=5)
def test_text_strategy_draws(data):
    """Test TextConformance.params_strategy() produces valid params."""
    binary_path = _make_conformance_binary(
        "mf.write(json.dumps({'length': 5}) + '\\n')",
    )
    try:
        tc = TextConformance(binary_path, test_cases=1)
        params = data.draw(tc.params_strategy())
        assert "min_size" in params
        assert "max_size" in params
    finally:
        os.unlink(binary_path)


@given(st.data())
@settings(max_examples=5)
def test_binary_strategy_draws(data):
    """Test BinaryConformance.params_strategy() produces valid params."""
    binary_path = _make_conformance_binary(
        "mf.write(json.dumps({'length': 5}) + '\\n')",
    )
    try:
        bc = BinaryConformance(binary_path, test_cases=1)
        params = data.draw(bc.params_strategy())
        assert "min_size" in params
        assert "max_size" in params
    finally:
        os.unlink(binary_path)


@given(st.data())
@settings(max_examples=5)
def test_list_strategy_draws(data):
    """Test ListConformance.params_strategy() produces valid params."""
    binary_path = _make_conformance_binary(
        "mf.write(json.dumps({'size': 1, 'min_element': 5, 'max_element': 5}) + '\\n')",
    )
    try:
        lc = ListConformance(binary_path, test_cases=1, min_value=0, max_value=100)
        params = data.draw(lc.params_strategy())
        assert "min_size" in params
        assert "max_size" in params
        assert "min_value" in params
        assert "max_value" in params
    finally:
        os.unlink(binary_path)


@given(st.data())
@settings(max_examples=5)
def test_sampled_from_strategy_draws(data):
    """Test SampledFromConformance.params_strategy() produces valid params."""
    binary_path = _make_conformance_binary(
        "mf.write(json.dumps({'value': 1}) + '\\n')",
    )
    try:
        sc = SampledFromConformance(binary_path, test_cases=1)
        params = data.draw(sc.params_strategy())
        assert "options" in params
        assert len(params["options"]) >= 1
    finally:
        os.unlink(binary_path)


@given(st.data())
@settings(max_examples=5)
def test_dict_strategy_draws(data):
    """Test DictConformance.params_strategy() produces valid params."""
    binary_path = _make_conformance_binary(
        "mf.write(json.dumps({'size': 0}) + '\\n')",
    )
    try:
        dc = DictConformance(binary_path, test_cases=1)
        params = data.draw(dc.params_strategy())
        assert "min_size" in params
        assert "max_size" in params
        assert "key_type" in params
        assert "min_key" in params
        assert "max_key" in params
        assert "min_value" in params
        assert "max_value" in params
    finally:
        os.unlink(binary_path)


def test_run_conformance_tests_function():
    """Test run_conformance_tests function end-to-end."""
    # Create a binary that handles all conformance test types
    script = (
        "import random\\n"
        "    for _ in range(test_cases):\\n"
        "        mf.write(json.dumps({\\n"
        "            'value': random.choice([True, False]),\\n"
        "            'length': 5,\\n"
        "            'size': 1,\\n"
        "            'min_element': 0,\\n"
        "            'max_element': 10,\\n"
        "            'min_key': 0,\\n"
        "            'max_key': 10,\\n"
        "            'min_value': 0,\\n"
        "            'max_value': 10,\\n"
        "        }) + '\\\\n')"
    )
    binary_path = _make_conformance_binary(script)
    try:

        tests = [
            BooleanConformance(binary_path, test_cases=2),
            IntegerConformance(binary_path, test_cases=2, min_value=0, max_value=10),
            FloatConformance(binary_path, test_cases=2),
            TextConformance(binary_path, test_cases=2),
            BinaryConformance(binary_path, test_cases=2),
            ListConformance(binary_path, test_cases=2, min_value=0, max_value=10),
            SampledFromConformance(binary_path, test_cases=2),
            DictConformance(binary_path, test_cases=2),
        ]

        # run_conformance_tests needs pytest.Subtests, which is hard to mock.
        # Instead test the assertion check for registered tests.
        assert {type(t) for t in tests} == ConformanceTest.registered_tests
    finally:
        os.unlink(binary_path)


def test_run_conformance_tests_full(subtests):
    """Test run_conformance_tests exercises the function structure."""
    from unittest.mock import MagicMock

    from hegel.conformance import run_conformance_tests

    binary_path = _make_conformance_binary(
        "mf.write(json.dumps({'value': True}) + '\\n')",
    )
    try:
        tests = [
            BooleanConformance(binary_path, test_cases=1),
            IntegerConformance(
                binary_path, test_cases=1,
                min_value=None, max_value=None,
            ),
            FloatConformance(binary_path, test_cases=1),
            TextConformance(binary_path, test_cases=1),
            BinaryConformance(binary_path, test_cases=1),
            ListConformance(binary_path, test_cases=1, min_value=None, max_value=None),
            SampledFromConformance(binary_path, test_cases=1),
            DictConformance(binary_path, test_cases=1),
        ]

        # Mock the run method on each test to avoid binary execution
        for t in tests:
            t.run = MagicMock()

        from hypothesis import settings as Settings

        run_conformance_tests(
            tests,
            subtests,
            settings=Settings(max_examples=1, deadline=None),
        )
    finally:
        os.unlink(binary_path)


def test_float_conformance_run():
    """Test FloatConformance.run() with actual binary."""
    script = (
        "for i in range(test_cases):\n"
        "        mf.write(json.dumps({'value': 1.5}) + '\\n')"
    )
    binary_path = _make_conformance_binary(script)
    try:
        fc = FloatConformance(binary_path, test_cases=2)
        fc.run({
            "min_value": 0.0,
            "max_value": 10.0,
            "exclude_min": False,
            "exclude_max": False,
            "allow_nan": False,
            "allow_infinity": False,
        })
    finally:
        os.unlink(binary_path)


def test_text_conformance_run():
    """Test TextConformance.run() with actual binary."""
    script = (
        "for i in range(test_cases):\n"
        "        mf.write(json.dumps({'length': 5}) + '\\n')"
    )
    binary_path = _make_conformance_binary(script)
    try:
        tc = TextConformance(binary_path, test_cases=2)
        tc.run({"min_size": 0, "max_size": 20})
    finally:
        os.unlink(binary_path)


def test_binary_conformance_run():
    """Test BinaryConformance.run() with actual binary."""
    script = (
        "for i in range(test_cases):\n"
        "        mf.write(json.dumps({'length': 5}) + '\\n')"
    )
    binary_path = _make_conformance_binary(script)
    try:
        bc = BinaryConformance(binary_path, test_cases=2)
        bc.run({"min_size": 0, "max_size": 20})
    finally:
        os.unlink(binary_path)


def test_list_conformance_run():
    """Test ListConformance.run() with actual binary."""
    script = (
        "for i in range(test_cases):\n"
        "        mf.write(json.dumps({"
        "'size': 2, 'min_element': 3, "
        "'max_element': 7}) + '\\n')"
    )
    binary_path = _make_conformance_binary(script)
    try:
        lc = ListConformance(binary_path, test_cases=2, min_value=0, max_value=100)
        lc.run({
            "min_size": 0,
            "max_size": 10,
            "min_value": 0,
            "max_value": 100,
        })
    finally:
        os.unlink(binary_path)


def test_sampled_from_conformance_run():
    """Test SampledFromConformance.run() with actual binary."""
    script = (
        "for i in range(test_cases):\n"
        "        mf.write(json.dumps({'value': 5}) + '\\n')"
    )
    binary_path = _make_conformance_binary(script)
    try:
        sc = SampledFromConformance(binary_path, test_cases=2)
        sc.run({"options": [1, 5, 10]})
    finally:
        os.unlink(binary_path)


def test_dict_conformance_run():
    """Test DictConformance.run() with actual binary."""
    script = (
        "for i in range(test_cases):\n"
        "        mf.write(json.dumps({"
        "'size': 1, 'min_key': 1, 'max_key': 1, "
        "'min_value': 5, 'max_value': 5}) + '\\n')"
    )
    binary_path = _make_conformance_binary(script)
    try:
        dc = DictConformance(binary_path, test_cases=2)
        dc.run({
            "min_size": 0,
            "max_size": 10,
            "key_type": "integer",
            "min_key": 0,
            "max_key": 100,
            "min_value": 0,
            "max_value": 100,
        })
    finally:
        os.unlink(binary_path)
