import hashlib
import json
import os
import shlex
import socket
import sys
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from shutil import which
from typing import Any

import click
from hypothesis import Verbosity, settings
from hypothesis.control import BuildContext
from hypothesis.database import DirectoryBasedExampleDatabase
from hypothesis.errors import StopTest, UnsatisfiedAssumption
from hypothesis.internal.conjecture.data import ConjectureData
from hypothesis.internal.conjecture.engine import ConjectureRunner
from hypothesis.internal.conjecture.shrinker import sort_key

from hegel.parser import from_schema
from hegel.runner import run_with_callback

DATABASE = DirectoryBasedExampleDatabase(".hegel")


def validate_command(ctx: Any, param: Any, value: str | None) -> list[str] | None:
    # Allow None value when --client-mode is used
    if value is None:
        return None

    parts = shlex.split(value)
    if not parts:
        raise click.BadParameter("command cannot be empty")
    command = parts[0]

    if os.path.exists(command):
        command = os.path.abspath(command)
    else:
        what = which(command)
        if what is None:
            raise click.BadParameter(f"{command}: command not found")
        command = os.path.abspath(what)
    return [command] + parts[1:]


def make_settings(test_cases: int, verbosity: Verbosity) -> settings:
    return settings(
        deadline=None,
        database=DATABASE,
        max_examples=test_cases,
        verbosity=verbosity,
    )


FROM_SCHEMA_CACHE: dict[bytes, Any] = OrderedDict()
CACHE_SIZE = 1024


def cached_from_schema(schema):
    key = hashlib.sha1(json.dumps(schema).encode("utf-8")).digest()[:32]
    try:
        result = FROM_SCHEMA_CACHE[key]
        FROM_SCHEMA_CACHE.move_to_end(key)
        return result
    except KeyError:
        result = from_schema(schema)
        FROM_SCHEMA_CACHE[key] = result
        if len(FROM_SCHEMA_CACHE) > CACHE_SIZE:
            FROM_SCHEMA_CACHE.popitem(last=False)
        return result


def make_test_function(
    test: list[str],
    rejected: int,
    on_result: Callable[[Any], None] | None = None,
    capture_output: bool = True,
    on_stdout_file: Callable[[str], None] | None = None,
) -> Callable[[ConjectureData], None]:
    def test_function(data: ConjectureData) -> None:
        with BuildContext(data, is_final=False, wrapped_test=None):  # type: ignore

            def handle_command(command: str, payload: Any) -> Any:
                if command == "generate":
                    return data.draw(cached_from_schema(payload))
                elif command == "start_span":
                    label = payload.get("label", 0)
                    data.start_span(label)
                    return None
                elif command == "stop_span":
                    discard = payload.get("discard", False)
                    data.stop_span(discard=discard)
                    return None
                else:
                    raise ValueError(f"Unknown command: {command}")

            @run_with_callback(
                test, capture_output=capture_output, on_stdout_file=on_stdout_file
            )
            def result(command, payload):
                return handle_command(command, payload)

            if on_result is not None:
                on_result(result)

            if result.exit_code != 0:
                if result.exit_code == rejected:
                    data.mark_invalid()
                else:
                    data.mark_interesting(result.exit_code)  # type: ignore

    return test_function


@dataclass
class HegelData:
    # TODO: This will change to a mapping of IDs to
    data: ConjectureData


def replay_failure(
    test: list[str], rejected: int, runner: ConjectureRunner, choices: Any
) -> int:
    test_function = make_test_function(test, rejected, capture_output=False)
    final_data = runner.new_conjecture_data(choices)
    try:
        test_function(final_data)
    except StopTest:
        pass
    io = final_data.interesting_origin
    assert isinstance(io, int)
    return io


def run_client_mode(
    socket_path: str,
    rejected: int,
    test_cases: int,
    verbosity: Verbosity,
) -> None:
    """Run hegel as a client, connecting to an SDK's server socket.

    In this mode, hegel connects to a server socket created by an SDK.
    Each test case is a separate connection. The protocol is:
    1. Hegel connects and sends handshake with is_last_run info
    2. SDK responds with handshake_ack
    3. SDK runs its test function, sending generate/span requests to hegel
    4. SDK sends test_result when done
    5. Connection closes

    This is the inverse of normal mode where hegel creates the socket
    and the test binary connects to it.
    """
    db_key = b"client_mode"

    def make_client_test_function(
        is_final_run: bool = False,
    ) -> Callable[[ConjectureData], None]:
        def test_function(data: ConjectureData) -> None:
            with BuildContext(data, is_final=is_final_run, wrapped_test=None):  # type: ignore

                def handle_command(command: str, payload: Any) -> Any:
                    if command == "generate":
                        return data.draw(cached_from_schema(payload))
                    elif command == "start_span":
                        label = payload.get("label", 0)
                        data.start_span(label)
                        return None
                    elif command == "stop_span":
                        discard = payload.get("discard", False)
                        data.stop_span(discard=discard)
                        return None
                    else:
                        raise ValueError(f"Unknown command: {command}")

                # Connect to SDK's server socket
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                try:
                    sock.connect(socket_path)
                except OSError as e:
                    print(
                        f"Failed to connect to socket {socket_path}: {e}",
                        file=sys.stderr,
                    )
                    data.mark_invalid()  # raises StopTest

                try:
                    # Send handshake
                    handshake = {"type": "handshake", "is_last_run": is_final_run}
                    sock.sendall((json.dumps(handshake) + "\n").encode())

                    # Read handshake_ack
                    reader = sock.makefile("rb")
                    ack_line = reader.readline()
                    if not ack_line:
                        print("No handshake_ack received", file=sys.stderr)
                        data.mark_invalid()

                    try:
                        ack = json.loads(ack_line.decode())
                    except json.JSONDecodeError:
                        print("Invalid handshake_ack", file=sys.stderr)
                        data.mark_invalid()

                    if verbosity == Verbosity.debug:
                        print(f"Handshake complete: {ack}", file=sys.stderr)

                    while True:
                        line = reader.readline()
                        if not line:
                            data.mark_invalid()  # raises StopTest

                        try:
                            request = json.loads(line.decode())
                        except json.JSONDecodeError:
                            # Skip invalid JSON lines and continue reading
                            continue

                        if verbosity == Verbosity.debug:
                            print(f"REQUEST: {request}", file=sys.stderr)

                        # Check for test_result message (end of test)
                        if request.get("type") == "test_result":
                            result = request.get("result")
                            if result == "reject":
                                data.mark_invalid()
                            elif result == "fail":
                                message = request.get("message", "Test failed")
                                if is_final_run:
                                    print(f"Test failed: {message}", file=sys.stderr)
                                data.mark_interesting(1)  # type: ignore
                            # result == "pass" means test passed, nothing to do
                            break

                        # Handle generate/span commands
                        command = request["command"]
                        payload = request["payload"]
                        request_id = request.get("id")

                        try:
                            result = handle_command(command, payload)
                            response = {"id": request_id, "result": result}
                        except ValueError as e:
                            # Unknown command - send error response
                            response = {"id": request_id, "error": str(e)}
                            if verbosity == Verbosity.debug:  # pragma: no branch
                                print(f"RESPONSE: {response}", file=sys.stderr)
                            sock.sendall((json.dumps(response) + "\n").encode())
                            continue
                        except (StopTest, UnsatisfiedAssumption):
                            # StopTest means hypothesis wants to stop this test case
                            # (e.g., buffer exhausted, marked invalid). Send special
                            # "reject" response so SDK knows to treat this as rejection.
                            reject_response = {
                                "id": request_id,
                                "reject": True,
                                "reason": "Test case stopped by hypothesis",
                            }
                            if verbosity == Verbosity.debug:
                                print(f"RESPONSE: {reject_response}", file=sys.stderr)
                            sock.sendall((json.dumps(reject_response) + "\n").encode())
                            raise

                        if verbosity == Verbosity.debug:
                            print(f"RESPONSE: {response}", file=sys.stderr)

                        sock.sendall((json.dumps(response) + "\n").encode())

                finally:
                    sock.close()

        return test_function

    test_function = make_client_test_function(is_final_run=False)

    runner = ConjectureRunner(
        test_function,
        settings=make_settings(test_cases, verbosity),
        database_key=db_key,
    )
    runner.run()

    if runner.interesting_examples:
        result = min(
            runner.interesting_examples.values(),
            key=lambda d: sort_key(d.nodes),
        )
        # Replay the failure with is_final_run=True so note() prints
        final_test = make_client_test_function(is_final_run=True)
        final_data = runner.new_conjecture_data(result.choices)
        try:
            final_test(final_data)
        except StopTest:
            pass
        sys.exit(1)


@click.command()
@click.argument("test", callback=validate_command, required=False)
@click.option("--rejected", default=137)
@click.option(
    "--verbosity",
    type=click.Choice(["quiet", "normal", "verbose", "debug"]),
    default="normal",
    help="Verbosity level: quiet, normal, verbose, or debug",
)
@click.option("--test-cases", default=1000)
@click.option("--tui/--no-tui", default=True, help="Run with terminal UI")
@click.option(
    "--client-mode",
    default=None,
    help="Connect to this socket path as client (embedded mode)",
)
def main(test, rejected, verbosity, test_cases, tui, client_mode):
    os.environ["HEGEL_REJECT_CODE"] = str(rejected)
    if verbosity == "debug":
        os.environ["HEGEL_DEBUG"] = "true"

    hypothesis_verbosity = Verbosity(verbosity)

    if client_mode:
        # Run in client mode - connect to an SDK's server socket
        run_client_mode(client_mode, rejected, test_cases, hypothesis_verbosity)
    elif test:
        # Run in normal mode - spawn test binary as subprocess
        db_key = json.dumps(test).encode("utf-8")
        if tui:
            _run_with_tui(test, rejected, test_cases, db_key)
        else:
            _run_without_tui(test, rejected, hypothesis_verbosity, test_cases, db_key)
    else:
        raise click.UsageError("Either TEST argument or --client-mode is required")


def _run_without_tui(
    test: list[str], rejected: int, verbosity: Verbosity, test_cases: int, db_key: bytes
) -> None:
    # Show output immediately for verbose/debug, capture for quiet/normal
    capture_output = verbosity in (Verbosity.quiet, Verbosity.normal)
    test_function = make_test_function(test, rejected, capture_output=capture_output)

    runner = ConjectureRunner(
        test_function,
        settings=make_settings(test_cases, verbosity),
        database_key=db_key,
    )
    runner.run()

    if runner.interesting_examples:
        result = min(
            runner.interesting_examples.values(),
            key=lambda d: sort_key(d.nodes),
        )
        sys.exit(replay_failure(test, rejected, runner, result.choices))


def _run_with_tui(test, rejected, test_cases, db_key):
    from hegel.tui import HegelApp, Stats

    exit_code = [0]
    final_runner = [None]
    final_choices = [None]

    # Reference to stdout file path, set by on_stdout_file callback
    stdout_file_ref = [None]

    # Shared state between worker thread and poll timer (protected by lock)
    lock = threading.Lock()
    state = {
        "saved_output": "",  # Complete output from most recently completed test
        "last_test_end_time": 0.0,  # When previous test ended
        "display_switched": False,  # Whether we've switched to current test's output
        "running": True,  # Whether tests are still running
    }

    def run_tests(app: HegelApp) -> None:
        def on_stdout_file(path: str) -> None:
            """Called by runner when stdout file is created."""
            stdout_file_ref[0] = path

        def on_result(result):
            """Called after each test case completes."""
            with lock:
                # Use result.output which contains the captured stdout
                # (the runner's temp directory may already be cleaned up)
                if result.output is not None:  # pragma: no branch
                    state["saved_output"] = result.output

                # Always update display with final output when test completes
                app.update_output(state["saved_output"])

                # Record when test ended, reset state for next test
                state["last_test_end_time"] = time.time()
                state["display_switched"] = False

        test_function = make_test_function(
            test, rejected, on_result=on_result, on_stdout_file=on_stdout_file
        )

        runner = ConjectureRunner(
            test_function,
            settings=make_settings(test_cases, Verbosity.quiet),
            database_key=db_key,
        )

        old_test_function = runner.test_function

        def wrapped_test_function(data: ConjectureData) -> None:
            try:
                old_test_function(data)
            finally:
                app.update_stats(
                    Stats(
                        call_count=runner.call_count,
                        valid=runner.valid_examples,
                        invalid=runner.invalid_examples,
                        failures=len(runner.interesting_examples),
                        phase="running",
                    )
                )

        runner.test_function = wrapped_test_function  # type: ignore

        app.update_stats(Stats(phase="running"))
        runner.run()

        with lock:
            state["running"] = False

        if runner.interesting_examples:
            result = min(
                runner.interesting_examples.values(),
                key=lambda d: sort_key(d.nodes),
            )

            io = result.interesting_origin
            assert isinstance(io, int)
            exit_code[0] = io
            final_runner[0] = runner
            final_choices[0] = result.choices

            app.update_stats(
                Stats(
                    call_count=runner.call_count,
                    valid=runner.valid_examples,
                    invalid=runner.invalid_examples,
                    failures=len(runner.interesting_examples),
                    phase="failed",
                )
            )
        else:
            app.update_stats(
                Stats(
                    call_count=runner.call_count,
                    valid=runner.valid_examples,
                    invalid=runner.invalid_examples,
                    failures=0,
                    phase="passed",
                )
            )

        app.finish(exit_code[0])

    def poll_output():
        """Poll stdout file and update display with rate limiting."""
        with lock:
            if not state["running"]:
                return

            stdout_file = stdout_file_ref[0]
            if stdout_file is None:
                return

            # Try to read current file content
            try:
                with open(stdout_file) as f:
                    current_content = f.read()
            except (FileNotFoundError, OSError):
                current_content = ""

            if state["display_switched"]:
                # Already displaying current test's output - update if changed
                if current_content:
                    app.update_output_sync(current_content)
            else:
                # Check if we should switch to new output
                elapsed = time.time() - state["last_test_end_time"]
                has_line = "\n" in current_content

                if elapsed >= 1.0 and has_line:
                    # Both conditions met - switch to new output
                    state["display_switched"] = True
                    app.update_output_sync(current_content)
                # Otherwise: keep showing saved_output (already displayed)

    app = HegelApp(run_tests, poll_callback=poll_output, poll_interval=0.1)
    app.run()

    if final_choices[0] is not None:
        replay_failure(test, rejected, final_runner[0], final_choices[0])

    sys.exit(app.exit_code)
