from pathlib import Path
from typing import Any

import pytest
from conftest import CppTestBinaries

from hegel.runner import SubprocessTimedOut, run_with_callback


def test_can_respond_to_the_client(cpp_binaries: CppTestBinaries):
    calls: list[Any] = []

    @run_with_callback([cpp_binaries.hello3])
    def result(command: str, payload: Any):
        assert command == "generate"
        assert payload == {"const": "hello"}
        calls.append(payload)
        return "world"

    assert len(calls) == 3
    assert result.output.count("world") == 3


def test_can_propagate_error(cpp_binaries: CppTestBinaries):
    @run_with_callback([cpp_binaries.hello3])
    def result(command: str, payload: Any):
        raise ValueError("Good day to you sir")

    assert "Good day" in result.output.strip()
    assert result.exit_code != 0


def test_can_respond_after_a_delay(cpp_binaries: CppTestBinaries):
    calls: list[Any] = []

    @run_with_callback([cpp_binaries.hello_slow])
    def result(command: str, payload: Any):
        assert command == "generate"
        assert payload == {"const": "hello"}
        calls.append(payload)
        return "world"

    assert len(calls) == 1
    assert result.output.count("world") == 1


def test_can_kill_too_slow_command(cpp_binaries: CppTestBinaries):
    with pytest.raises(SubprocessTimedOut):

        @run_with_callback([cpp_binaries.hello_slow], timeout=0.1)
        def result(command: str, payload: Any):
            pass


RUDE_SCRIPT = """
#!/usr/bin/env python
from time import sleep

while True:
    try:
        sleep(0.5)
    except BaseException:
        pass
""".strip()


BAD_COMMAND_SCRIPT = """
#!/usr/bin/env python
import json, os, socket, sys

sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
sock.connect(os.environ["HEGEL_SOCKET"])
msg = json.dumps({"id": 1, "command": "unknown", "payload": {}})
sock.sendall((msg + "\\n").encode())

response = b""
while b"\\n" not in response:
    chunk = sock.recv(4096)
    if not chunk:
        break
    response += chunk
sock.close()

parsed = json.loads(response)
if "error" in parsed:
    print(parsed["error"], file=sys.stderr)
    sys.exit(1)
""".strip()


NO_NEWLINE_SCRIPT = """
#!/usr/bin/env python
import json, os, socket, sys

sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
sock.connect(os.environ["HEGEL_SOCKET"])
sock.sendall(b"not valid json")
sock.shutdown(socket.SHUT_WR)

response = b""
while True:
    chunk = sock.recv(4096)
    if not chunk:
        break
    response += chunk
sock.close()

parsed = json.loads(response)
if "error" in parsed:
    print(parsed["error"], file=sys.stderr)
    sys.exit(1)
""".strip()


BAD_JSON_SCRIPT = """
#!/usr/bin/env python
import json, os, socket, sys

sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
sock.connect(os.environ["HEGEL_SOCKET"])
sock.sendall(b"not valid json\\n")

response = b""
while True:
    chunk = sock.recv(4096)
    if not chunk:
        break
    response += chunk
    if b"\\n" in response:
        break
sock.close()

parsed = json.loads(response)
if "error" in parsed:
    print(parsed["error"], file=sys.stderr)
    sys.exit(1)
""".strip()


QUICK_EXIT_SCRIPT = """
#!/usr/bin/env python
import os, socket, sys

sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
sock.connect(os.environ["HEGEL_SOCKET"])
sock.close()
sys.exit(0)
""".strip()


SLOW_CLIENT_SCRIPT = """
#!/usr/bin/env python
import os, socket, time, signal

sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
sock.connect(os.environ["HEGEL_SOCKET"])

signal.signal(signal.SIGALRM, lambda *_: os._exit(0))
signal.setitimer(signal.ITIMER_REAL, 0.12)

while True:
    time.sleep(1)
""".strip()


NO_CONNECT_SCRIPT = """
#!/usr/bin/env python
import sys
sys.exit(0)
""".strip()


def test_can_kill_too_slow_command_even_if_it_is_very_rude(tmp_path: Path):
    script = tmp_path / "hello.py"
    _ = script.write_text(RUDE_SCRIPT)
    script.chmod(0o700)

    with pytest.raises(SubprocessTimedOut):

        @run_with_callback([str(script)], timeout=0.1)
        def result(command: str, payload: Any):
            pass


def test_unknown_command_returns_error(tmp_path: Path):
    """Test that the server returns an error for unknown commands."""
    script = tmp_path / "bad_command.py"
    script.write_text(BAD_COMMAND_SCRIPT)
    script.chmod(0o700)

    @run_with_callback(["python", str(script)])
    def result(command: str, payload: Any):
        if command != "generate":
            raise ValueError(f"Unknown command: {command}")
        return "should not be called"

    assert result.exit_code != 0
    assert "Unknown command" in result.output


def test_malformed_request_without_newline(tmp_path: Path):
    """Test that the server handles a client that closes without newline."""
    script = tmp_path / "no_newline.py"
    script.write_text(NO_NEWLINE_SCRIPT)
    script.chmod(0o700)

    @run_with_callback(["python", str(script)])
    def result(command: str, payload: Any):
        return "should not be called"

    # Client exits non-zero on receiving error
    assert result.exit_code != 0
    assert "Invalid JSON" in result.output


def test_malformed_json_with_newline(tmp_path: Path):
    """Test that the server handles malformed JSON that's properly terminated."""
    script = tmp_path / "bad_json.py"
    script.write_text(BAD_JSON_SCRIPT)
    script.chmod(0o700)

    @run_with_callback(["python", str(script)])
    def result(command: str, payload: Any):
        return "should not be called"

    # Client exits non-zero on receiving error
    assert result.exit_code != 0
    assert "Invalid JSON" in result.output


def test_client_exits_without_sending_data(tmp_path: Path):
    """Test that the server handles a client that connects then exits immediately."""
    script = tmp_path / "quick_exit.py"
    script.write_text(QUICK_EXIT_SCRIPT)
    script.chmod(0o700)

    @run_with_callback(["python", str(script)])
    def result(command: str, payload: Any):
        return "should not be called"

    # Client exits cleanly without sending any requests
    assert result.exit_code == 0


def test_client_causes_socket_timeout(tmp_path: Path):
    """Test that the server handles socket timeout when client is slow."""
    script = tmp_path / "slow_client.py"
    script.write_text(SLOW_CLIENT_SCRIPT)
    script.chmod(0o700)

    @run_with_callback(["python", str(script)])
    def result(command: str, payload: Any):
        return "should not be called"

    # Client exits cleanly after causing server to timeout
    assert result.exit_code == 0


def test_client_exits_without_connecting(tmp_path: Path):
    """Test that the server handles a client that exits without connecting to socket."""
    script = tmp_path / "no_connect.py"
    script.write_text(NO_CONNECT_SCRIPT)
    script.chmod(0o700)

    @run_with_callback(["python", str(script)])
    def result(command: str, payload: Any):
        return "should not be called"

    # Client exits cleanly without ever connecting
    assert result.exit_code == 0


def test_popen_failure_with_nonexistent_command():
    """Test that run_with_callback handles Popen failure gracefully."""
    with pytest.raises(FileNotFoundError):

        @run_with_callback(["/nonexistent/command/that/does/not/exist"])
        def result(command: str, payload: Any):
            return "should not be called"
