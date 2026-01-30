"""Tests for runner.py uncovered paths."""

import math
import sys

import pytest

from hegel.runner import HegelEncoder, convert_json, run_with_callback


def test_convert_json_inf():
    """Test convert_json handles positive infinity."""
    assert convert_json(math.inf) == {"$float": "inf"}


def test_convert_json_neg_inf():
    """Test convert_json handles negative infinity."""
    assert convert_json(-math.inf) == {"$float": "-inf"}


def test_convert_json_nan():
    """Test convert_json handles NaN."""
    result = convert_json(math.nan)
    assert result == {"$float": "nan"}


def test_convert_json_large_int():
    """Test convert_json handles large integers."""
    big = 2**63
    assert convert_json(big) == {"$integer": str(big)}
    assert convert_json(-big) == {"$integer": str(-big)}


def test_convert_json_normal_int():
    """Test convert_json passes through normal integers."""
    assert convert_json(42) == 42


def test_convert_json_bool_not_converted():
    """Test convert_json doesn't convert bools as large ints."""
    assert convert_json(value=True) is True
    assert convert_json(value=False) is False


def test_convert_json_dict():
    """Test convert_json recurses into dicts."""
    result = convert_json({"a": math.inf, "b": 2**64})
    assert result == {"a": {"$float": "inf"}, "b": {"$integer": str(2**64)}}


def test_convert_json_list():
    """Test convert_json recurses into lists."""
    result = convert_json([math.inf, 42, -math.inf])
    assert result == [{"$float": "inf"}, 42, {"$float": "-inf"}]


def test_convert_json_passthrough():
    """Test convert_json passes through strings and other types."""
    assert convert_json("hello") == "hello"
    assert convert_json(None) is None


def test_hegel_encoder_set():
    """Test HegelEncoder converts sets to lists."""
    import json

    result = json.loads(json.dumps({1, 2, 3}, cls=HegelEncoder))
    assert sorted(result) == [1, 2, 3]


def test_hegel_encoder_frozenset():
    """Test HegelEncoder converts frozensets to lists."""
    import json

    result = json.loads(json.dumps(frozenset([1, 2]), cls=HegelEncoder))
    assert sorted(result) == [1, 2]


def test_run_with_callback_basic():
    """Test run_with_callback with a simple echo command."""
    result = run_with_callback(
        [sys.executable, "-c", "pass"],
        timeout=10,
    )(lambda cmd, payload: None)

    assert result.exit_code == 0


def test_run_with_callback_no_capture():
    """Test run_with_callback without output capture."""
    result = run_with_callback(
        [sys.executable, "-c", "pass"],
        timeout=10,
        capture_output=False,
    )(lambda cmd, payload: None)

    assert result.exit_code == 0
    assert result.output is None


def test_run_with_callback_on_stdout_file():
    """Test run_with_callback calls on_stdout_file."""
    captured_path = []

    result = run_with_callback(
        [sys.executable, "-c", "print('hello')"],
        timeout=10,
        on_stdout_file=lambda path: captured_path.append(path),
    )(lambda cmd, payload: None)

    assert result.exit_code == 0
    assert len(captured_path) == 1
    assert "stdout" in captured_path[0]


def test_run_with_callback_with_socket_interaction():
    """Test run_with_callback with actual socket communication."""
    script = """
import json, os, socket

sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
sock.connect(os.environ["HEGEL_SOCKET"])
msg = {"id": 1, "command": "echo", "payload": {"msg": "hi"}}
sock.sendall(json.dumps(msg).encode() + b"\\n")

response = b""
while b"\\n" not in response:
    chunk = sock.recv(4096)
    if not chunk:
        break
    response += chunk

sock.close()
"""
    result = run_with_callback(
        [sys.executable, "-c", script],
        timeout=10,
    )(lambda cmd, payload: {"echoed": payload})

    assert result.exit_code == 0


def test_run_with_callback_error_in_callback():
    """Test that exceptions in callback are returned as errors."""
    script = """
import json, os, socket

sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
sock.connect(os.environ["HEGEL_SOCKET"])
sock.sendall(json.dumps({"id": 1, "command": "fail", "payload": {}}).encode() + b"\\n")

response = b""
while b"\\n" not in response:
    chunk = sock.recv(4096)
    if not chunk:
        break
    response += chunk

parsed = json.loads(response.strip())
assert "error" in parsed
sock.close()
"""
    result = run_with_callback(
        [sys.executable, "-c", script],
        timeout=10,
    )(lambda cmd, payload: (_ for _ in ()).throw(ValueError("boom")))

    assert result.exit_code == 0


def test_run_with_callback_invalid_json():
    """Test handling of invalid JSON from subprocess."""
    script = """
import os, socket

sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
sock.connect(os.environ["HEGEL_SOCKET"])
sock.sendall(b"not valid json\\n")

response = b""
while b"\\n" not in response:
    chunk = sock.recv(4096)
    if not chunk:
        break
    response += chunk

sock.close()
"""
    result = run_with_callback(
        [sys.executable, "-c", script],
        timeout=10,
    )(lambda cmd, payload: None)

    assert result.exit_code == 0


def test_run_with_callback_incomplete_request():
    """Test handling of incomplete data (no newline) when client closes."""
    script = """
import os, socket

sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
sock.connect(os.environ["HEGEL_SOCKET"])
# Send data without a newline and close
sock.sendall(b"incomplete data")
sock.close()
"""
    result = run_with_callback(
        [sys.executable, "-c", script],
        timeout=10,
    )(lambda cmd, payload: None)

    assert result.exit_code == 0


def test_convert_json_normal_float():
    """Test convert_json passes through normal floats."""
    assert convert_json(3.14) == 3.14
    assert convert_json(0.0) == 0.0
    assert convert_json(-1.5) == -1.5


def test_hegel_encoder_unknown_type():
    """Test HegelEncoder falls through to default for unknown types."""
    import json

    with pytest.raises(TypeError):
        json.dumps(object(), cls=HegelEncoder)
