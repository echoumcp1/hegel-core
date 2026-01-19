import json
import os
import random
import signal
import socket
import subprocess
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from tempfile import TemporaryDirectory
from time import sleep, time
from typing import Any


def signal_group(sp: subprocess.Popen, signal: int) -> None:
    gid = os.getpgid(sp.pid)
    assert gid != os.getgid()
    os.killpg(gid, signal)


class SubprocessTimedOut(Exception):
    pass


def interrupt_wait_and_kill(sp: subprocess.Popen, delay: float = 0.1) -> None:
    if sp.returncode is None:
        try:
            # In case the subprocess forked. Python might hang if you don't close
            # all pipes.
            for pipe in [sp.stdout, sp.stderr, sp.stdin]:
                if pipe:
                    pipe.close()
            signal_group(sp, signal.SIGINT)
            for n in range(10):
                if sp.poll() is not None:
                    return
                sleep(delay * 1.5**n * random.random())
        except ProcessLookupError:  # pragma: no cover
            # This is incredibly hard to trigger reliably, because it only happens
            # if the process exits at exactly the wrong time.
            pass

        # The only way to trigger this is if the process exits exactly after
        # the last sleep, which is impossible to test reliably (without the
        # determinator anyway)
        if sp.returncode is None:  # pragma: no branch
            try:
                signal_group(sp, signal.SIGKILL)
            except ProcessLookupError:  # pragma: no cover
                pass

        _ = sp.wait(timeout=delay)

        if sp.returncode is None:
            raise AssertionError(
                f"Could not kill subprocess with pid {sp.pid}. Something has gone seriously wrong."
            )


@dataclass
class Result:
    exit_code: int
    output: str | None


def run_with_callback(
    command: list[str],
    *,
    timeout=300,
    capture_output=True,
    on_stdout_file: Callable[[str], None] | None = None,
):
    """Run a command with a callback mechanism via Unix socket.

    Args:
        command: The command to run
        timeout: Maximum time to wait for the command
        capture_output: Whether to capture stdout/stderr
        on_stdout_file: Callback invoked with the stdout file path when it's created
    """

    def accept(callback_function: Callable[[Any], Any]):
        with TemporaryDirectory() as d:
            socket_path = os.path.join(d, f"callback.{callback_function.__name__}.sock")
            cwd = os.path.join(d, "cwd")
            os.makedirs(cwd)

            # Server side
            server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server_socket.settimeout(0.1)
            server_socket.bind(socket_path)
            server_socket.listen(1)
            env = dict(os.environ)

            env["HEGEL_SOCKET"] = socket_path

            if capture_output:
                stdout_path = os.path.join(d, "stdout")
                if on_stdout_file is not None:
                    on_stdout_file(stdout_path)
                out = open(stdout_path, "wb")
                err = out
            else:
                stdout_path = None
                out = None
                err = None

            sp = None
            try:
                sp = subprocess.Popen(
                    command,
                    stdout=out,
                    stderr=err,
                    env=env,
                    cwd=cwd,
                    universal_newlines=False,
                    preexec_fn=os.setsid,
                )
                if capture_output:
                    out.close()
                start = time()
                while time() <= start + timeout:
                    try:
                        conn, _ = server_socket.accept()
                    except TimeoutError:
                        if sp.poll() is not None:
                            break
                        continue

                    # Handle multiple requests on this connection until client closes
                    conn.settimeout(0.1)
                    buffer = bytearray()
                    connection_open = True

                    while connection_open and time() <= start + timeout:
                        # Check if we already have a complete line in buffer
                        while b"\n" in buffer:
                            line, _, buffer = buffer.partition(b"\n")

                            try:
                                request = json.loads(line)
                            except json.JSONDecodeError as e:
                                response = {"id": None, "error": f"Invalid JSON: {e}"}
                                conn.sendall(
                                    json.dumps(response, ensure_ascii=True).encode(
                                        "utf-8"
                                    )
                                    + b"\n"
                                )
                                continue

                            # Extract request fields
                            request_id = request.get("id")
                            request_command = request.get("command")
                            payload = request.get("payload")

                            try:
                                response = {
                                    "id": request_id,
                                    "result": callback_function(
                                        request_command, payload
                                    ),
                                }
                            except Exception:
                                response = {
                                    "id": request_id,
                                    "error": traceback.format_exc(),
                                }

                            conn.sendall(
                                json.dumps(response, ensure_ascii=True).encode("utf-8")
                                + b"\n"
                            )

                        # Try to read more data
                        try:
                            chunk = conn.recv(4096)
                            if not chunk:
                                # Client closed connection
                                # If there's leftover data without newline, treat as invalid
                                if buffer:
                                    response = {
                                        "id": None,
                                        "error": "Invalid JSON: incomplete request (no newline)",
                                    }
                                    conn.sendall(
                                        json.dumps(response, ensure_ascii=True).encode(
                                            "utf-8"
                                        )
                                        + b"\n"
                                    )
                                connection_open = False
                            else:
                                buffer.extend(chunk)
                        except TimeoutError:
                            # No data available, check if subprocess exited.
                            # This is a race condition: we only reach here if recv() times out
                            # (socket still open, no data) AND the subprocess has exited.
                            # Normally when a subprocess exits, the OS closes its sockets,
                            # causing recv() to return empty bytes rather than timing out.
                            # This path handles the narrow window where the timeout fires
                            # just as the process is exiting but before socket cleanup.
                            if sp.poll() is not None:
                                connection_open = False  # pragma: no cover

                    conn.close()
                else:
                    raise SubprocessTimedOut(
                        f"Command {command} exceeded timeout of {timeout}"
                    )
            finally:
                if sp is not None:
                    interrupt_wait_and_kill(sp)
                server_socket.close()

            if capture_output:
                with open(stdout_path) as out:
                    return Result(
                        exit_code=sp.returncode,
                        output=out.read(),
                    )
            else:
                return Result(exit_code=sp.returncode, output=None)

    return accept
