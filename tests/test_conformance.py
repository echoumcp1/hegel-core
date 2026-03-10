import os
import stat
import sys
import tempfile

import pytest

from hegel.conformance import BooleanConformance, TextConformance


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


def test_run_failure(conformance_binary):
    binary_path = conformance_binary("sys.exit(1)")
    test = BooleanConformance(binary_path, test_cases=1)
    with pytest.raises(RuntimeError, match="exit code"):
        test.run({})


@pytest.mark.parametrize(
    "char",
    [
        "\x85",  # next line (NEL)
        "\u2028",  # line separator
        "\u2029",  # paragraph separator
    ],
)
def test_jsonl_parsing_does_not_split_on_unicode_line_boundaries(
    char, conformance_binary
):
    # Construct a raw JSON line with the literal character embedded inside a
    # string value. Python's json.dumps escapes control characters, so we
    # build the JSON by hand to simulate what a non-Python JSON library that
    # doesn't escape these characters might produce.
    s = '{"length": 5, "text": "hello' + char + 'world"}'
    assert len(s.splitlines()) > 1

    binary_path = conformance_binary(f"mf.write({s!r} + '\\n')")
    conformance = TextConformance(binary_path)
    conformance.run({"min_size": 0, "max_size": None})
