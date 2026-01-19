import subprocess
from pathlib import Path

from conftest import CppTestBinaries

from hegel.__main__ import CACHE_SIZE, FROM_SCHEMA_CACHE, cached_from_schema

UNKNOWN_COMMAND_SCRIPT = """
#!/usr/bin/env python
import json, os, socket, sys

sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
sock.connect(os.environ["HEGEL_SOCKET"])
sock.sendall((json.dumps({"id": 1, "command": "unknown_command", "payload": {}}) + "\\n").encode())

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


SPAN_COMMANDS_SCRIPT = """
#!/usr/bin/env python
import json, os, socket, sys

sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
sock.connect(os.environ["HEGEL_SOCKET"])

def send_request(request_id, command, payload):
    request = {"id": request_id, "command": command, "payload": payload}
    sock.sendall((json.dumps(request) + "\\n").encode())
    response = b""
    while b"\\n" not in response:
        chunk = sock.recv(4096)
        if not chunk:
            break
        response += chunk
    return json.loads(response.split(b"\\n")[0])

response = send_request(1, "start_span", {"label": 42})
if "error" in response:
    print(f"start_span error: {response['error']}", file=sys.stderr)
    sys.exit(1)

response = send_request(2, "generate", {"const": "hello"})
if "error" in response:
    print(f"generate error: {response['error']}", file=sys.stderr)
    sys.exit(1)

response = send_request(3, "stop_span", {"discard": False})
if "error" in response:
    print(f"stop_span error: {response['error']}", file=sys.stderr)
    sys.exit(1)

sock.close()
sys.exit(0)
""".strip()


def test_will_run_a_script_to_completion(cpp_binaries: CppTestBinaries):
    result = subprocess.run(
        ["hegel", "--no-tui", cpp_binaries.hfear],
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "Oh no, an h!"
    # The observe() call prints "s: <value>" where value contains 'h'
    assert "s: " in result.stderr
    assert "h" in result.stderr
    assert result.returncode == 3


def test_will_read_spec_from_stdin(cpp_binaries: CppTestBinaries):
    result = subprocess.run(
        ["hegel", "--no-tui", cpp_binaries.const42],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0


def test_will_run_a_multi_part_command(cpp_binaries: CppTestBinaries):
    result = subprocess.run(
        ["hegel", "--no-tui", f"bash -c '{cpp_binaries.const42}'"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0


def test_will_fail_on_a_non_existent_command():
    result = subprocess.run(
        ["hegel", "--no-tui", "fwofa"], capture_output=True, text=True
    )
    assert result.returncode != 0
    # The argument is now optional (for --client-mode), so it shows as '[TEST]'
    assert "fwofa: command not found" in result.stderr.strip()


def test_will_fail_on_empty_command():
    result = subprocess.run(["hegel", "--no-tui", ""], capture_output=True, text=True)
    assert result.returncode != 0
    assert "command cannot be empty" in result.stderr


def test_handles_rejected_test_cases(cpp_binaries: CppTestBinaries):
    result = subprocess.run(
        ["hegel", "--no-tui", "--test-cases", "100", cpp_binaries.reject],
        capture_output=True,
        text=True,
    )
    # Should complete without finding a failure (all rejected)
    assert result.returncode == 0


def test_can_run_command_via_path_lookup(cpp_binaries: CppTestBinaries):
    """Test that hegel can find commands via PATH lookup (not just absolute paths)."""
    # Use 'env' command which should exist on PATH and can run our binary
    result = subprocess.run(
        ["hegel", "--no-tui", f"env {cpp_binaries.const42}"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0


def test_unknown_command_error(tmp_path: Path):
    """Test that hegel returns error for unknown commands from client."""
    script = tmp_path / "unknown_command.py"
    script.write_text(UNKNOWN_COMMAND_SCRIPT)
    script.chmod(0o700)

    result = subprocess.run(
        ["hegel", "--no-tui", "--test-cases", "1", f"python {script}"],
        capture_output=True,
        text=True,
    )
    # Should fail because the client sends an unknown command
    assert result.returncode != 0
    assert "Unknown command" in result.stderr


def test_span_commands(tmp_path: Path):
    """Test that hegel handles start_span and stop_span commands."""
    script = tmp_path / "span_commands.py"
    script.write_text(SPAN_COMMANDS_SCRIPT)
    script.chmod(0o700)

    result = subprocess.run(
        ["hegel", "--no-tui", "--test-cases", "1", f"python {script}"],
        capture_output=True,
        text=True,
    )
    # Just verify it runs successfully - span commands are handled without error
    assert result.returncode == 0


def test_debug_verbosity_shows_output(cpp_binaries: CppTestBinaries):
    """Test that --verbosity debug causes output to be shown."""
    result = subprocess.run(
        [
            "hegel",
            "--no-tui",
            "--verbosity",
            "debug",
            "--test-cases",
            "1",
            cpp_binaries.const42,
        ],
        capture_output=True,
        text=True,
    )
    # Debug verbosity should complete successfully
    assert result.returncode == 0


def test_on_result_callback_is_called(cpp_binaries: CppTestBinaries):
    """Test that on_result callback is invoked when provided."""
    from hypothesis import Verbosity
    from hypothesis.internal.conjecture.engine import ConjectureRunner

    from hegel.__main__ import make_settings, make_test_function

    results_received = []

    def capture_result(result):
        results_received.append(result)

    test_function = make_test_function(
        [cpp_binaries.const42],
        rejected=137,
        on_result=capture_result,
    )

    runner = ConjectureRunner(
        test_function,
        settings=make_settings(1, Verbosity.quiet),
        database_key=b"test",
    )
    runner.run()

    # The callback should have been called at least once
    assert len(results_received) >= 1
    # Each result should be a Result object with exit_code
    assert all(hasattr(r, "exit_code") for r in results_received)


def test_cache_eviction():
    for i in range(CACHE_SIZE * 2):
        schema = {"type": "integer", "minimum": i}
        strat = cached_from_schema(schema)
        # This is a stupid way to test if we've got the righ
        # strategy, but that's mostly not what we're testing
        # here anyway.
        assert repr(strat) == f"integers(min_value={i})"
        assert len(FROM_SCHEMA_CACHE) <= CACHE_SIZE
