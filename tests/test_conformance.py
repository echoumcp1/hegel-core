import os
import stat
import sys
import tempfile

import pytest

from hegel.conformance import BooleanConformance


def _make_conformance_binary(script_body):
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


def test_nonexistent_binary():
    with pytest.raises(AssertionError):
        BooleanConformance("/nonexistent/path/to/binary")


def test_default_test_cases(conformance_binary):
    binary_path = conformance_binary(
        "mf.write(json.dumps({'value': True}) + '\\n')",
    )
    bc = BooleanConformance(binary_path)
    assert bc.test_cases == BooleanConformance.default_test_cases


def test_run_failure(conformance_binary):
    binary_path = conformance_binary("sys.exit(1)")
    bc = BooleanConformance(binary_path, test_cases=1)
    with pytest.raises(RuntimeError, match="exit code"):
        bc.run({})
