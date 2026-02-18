"""Smoke tests for conformance.py."""

import os
import stat
import sys
import tempfile
from unittest.mock import MagicMock

import pytest
from hypothesis import settings

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
    run_conformance_tests,
)


def _make_conformance_binary(script_body):
    """Create a temporary executable Python script for conformance testing."""
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".py",
        delete=False,
        prefix="conform_",
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


@pytest.fixture
def conformance_binary():
    paths = []

    def make(script_body):
        path = _make_conformance_binary(script_body)
        paths.append(path)
        return path

    yield make
    for p in paths:
        os.unlink(p)


def test_registered_tests():
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


def test_nonexistent_binary():
    with pytest.raises(AssertionError):
        BooleanConformance("/nonexistent/path/to/binary")


def test_default_test_cases(conformance_binary):
    binary_path = conformance_binary(
        "mf.write(json.dumps({'value': True}) + '\\n')",
    )
    bc = BooleanConformance(binary_path)
    assert bc.test_cases == BooleanConformance.default_test_cases


def test_boolean_run(conformance_binary):
    binary_path = conformance_binary(
        "mf.write(json.dumps({'value': True}) + '\\n')",
    )
    bc = BooleanConformance(binary_path, test_cases=1)
    bc.run({})


def test_run_failure(conformance_binary):
    binary_path = conformance_binary("sys.exit(1)")
    bc = BooleanConformance(binary_path, test_cases=1)
    with pytest.raises(RuntimeError, match="exit code"):
        bc.run({})


def test_integer_run(conformance_binary):
    script = (
        "for i in range(test_cases):\n"
        "        mf.write(json.dumps({'value': 5}) + '\\n')"
    )
    binary_path = conformance_binary(script)
    ic = IntegerConformance(binary_path, test_cases=2, min_value=0, max_value=10)
    ic.run({"min_value": 0, "max_value": 10})


def test_float_run(conformance_binary):
    script = (
        "for i in range(test_cases):\n"
        "        mf.write(json.dumps({'value': 1.5}) + '\\n')"
    )
    binary_path = conformance_binary(script)
    fc = FloatConformance(binary_path, test_cases=2)
    fc.run(
        {
            "min_value": 0.0,
            "max_value": 10.0,
            "exclude_min": False,
            "exclude_max": False,
            "allow_nan": False,
            "allow_infinity": False,
        },
    )


def test_text_run(conformance_binary):
    script = (
        "for i in range(test_cases):\n"
        "        mf.write(json.dumps({'length': 5}) + '\\n')"
    )
    binary_path = conformance_binary(script)
    tc = TextConformance(binary_path, test_cases=2)
    tc.run({"min_size": 0, "max_size": 20})


def test_binary_run(conformance_binary):
    script = (
        "for i in range(test_cases):\n"
        "        mf.write(json.dumps({'length': 5}) + '\\n')"
    )
    binary_path = conformance_binary(script)
    bc = BinaryConformance(binary_path, test_cases=2)
    bc.run({"min_size": 0, "max_size": 20})


def test_list_run(conformance_binary):
    script = (
        "for i in range(test_cases):\n"
        "        mf.write(json.dumps({"
        "'size': 2, 'min_element': 3, "
        "'max_element': 7}) + '\\n')"
    )
    binary_path = conformance_binary(script)
    lc = ListConformance(binary_path, test_cases=2, min_value=0, max_value=100)
    lc.run(
        {"min_size": 0, "max_size": 10, "min_value": 0, "max_value": 100},
    )


def test_sampled_from_run(conformance_binary):
    script = (
        "for i in range(test_cases):\n"
        "        mf.write(json.dumps({'value': 5}) + '\\n')"
    )
    binary_path = conformance_binary(script)
    sc = SampledFromConformance(binary_path, test_cases=2)
    sc.run({"options": [1, 5, 10]})


def test_dict_run(conformance_binary):
    script = (
        "for i in range(test_cases):\n"
        "        mf.write(json.dumps({"
        "'size': 1, 'min_key': 1, 'max_key': 1, "
        "'min_value': 5, 'max_value': 5}) + '\\n')"
    )
    binary_path = conformance_binary(script)
    dc = DictConformance(binary_path, test_cases=2)
    dc.run(
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


def test_run_conformance_tests(subtests, conformance_binary):
    binary_path = conformance_binary(
        "mf.write(json.dumps({'value': True}) + '\\n')",
    )
    tests = [
        BooleanConformance(binary_path, test_cases=1),
        IntegerConformance(binary_path, test_cases=1, min_value=None, max_value=None),
        FloatConformance(binary_path, test_cases=1),
        TextConformance(binary_path, test_cases=1),
        BinaryConformance(binary_path, test_cases=1),
        ListConformance(binary_path, test_cases=1, min_value=None, max_value=None),
        SampledFromConformance(binary_path, test_cases=1),
        DictConformance(binary_path, test_cases=1),
    ]

    for t in tests:
        t.run = MagicMock()

    run_conformance_tests(
        tests,
        subtests,
        settings=settings(max_examples=1, deadline=None),
    )
