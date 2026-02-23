import atexit
import contextlib
import functools
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from typing import Any, TypeVar

from hegel.protocol import Connection
from hegel_sdk.client import Client

F = TypeVar("F", bound=Callable[..., Any])


def _find_hegeld() -> str:
    """Find the hegeld binary path."""
    if sys.prefix != sys.base_prefix:
        venv_hegel = os.path.join(sys.prefix, "bin", "hegel")
        if os.path.exists(venv_hegel):
            return venv_hegel

    hegel_path = shutil.which("hegel")
    if hegel_path:
        return hegel_path

    return f"{sys.executable} -m hegel"


class _HegelSession:
    """Manages a shared hegeld subprocess for the test suite.

    Spawns hegeld once on first use and keeps it running for all tests.
    Cleans up automatically when the process exits.
    """

    def __init__(self):
        self._process: subprocess.Popen | None = None
        self._sock: socket.socket | None = None
        self._connection: Connection | None = None
        self._client: Client | None = None
        self._temp_dir: tempfile.TemporaryDirectory | None = None
        self.__lock = threading.Lock()

    def __has_working_client(self):
        return self._client is not None and self._connection.live

    def _start(self) -> None:
        """Start hegeld if not already running."""
        if self.__has_working_client():
            return

        with self.__lock:
            if self.__has_working_client():
                return
            self._temp_dir = tempfile.TemporaryDirectory(prefix="hegel-")
            socket_path = os.path.join(self._temp_dir.name, "hegel.sock")

            hegel_cmd = _find_hegeld()
            cmd_args = [
                *hegel_cmd.split(),
                socket_path,
            ]

            # Start hegeld - it will bind to the socket and listen
            self._process = subprocess.Popen(
                cmd_args,
                stdout=sys.stderr,
                stderr=sys.stderr,
            )

            # Wait for hegeld to create the socket and start listening
            for _ in range(50):
                if os.path.exists(socket_path):
                    try:
                        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                        sock.connect(socket_path)
                        self._sock = sock
                        break
                    except (ConnectionRefusedError, FileNotFoundError):
                        sock.close()
                        time.sleep(0.1)
                else:
                    time.sleep(0.1)
            else:
                self._process.kill()
                raise RuntimeError("Timeout waiting for hegeld to start")

            self._connection = Connection(self._sock, name="SDK")
            self._client = Client(self._connection)

            # Register cleanup on process exit
            atexit.register(self._cleanup)

    def _cleanup(self) -> None:
        """Clean up the hegeld process."""
        if self._connection is not None:
            with contextlib.suppress(Exception):
                self._connection.close()
            self._connection = None
            self._client = None

        if self._process is not None:
            with contextlib.suppress(Exception):
                self._process.terminate()
                self._process.wait(timeout=5)
            self._process = None

        if self._sock is not None:
            with contextlib.suppress(Exception):
                self._sock.close()
            self._sock = None

        if self._temp_dir is not None:
            with contextlib.suppress(Exception):
                self._temp_dir.cleanup()
            self._temp_dir = None

    def run_test(
        self,
        test_fn: Callable[[], None],
        test_cases: int,
        seed: int | None
    ) -> None:
        """Run a property test using the shared hegeld process."""
        self._start()

        assert self._client is not None
        test_name = test_fn.__name__ if hasattr(test_fn, "__name__") else "test"
        self._client.run_test(test_name, test_fn, test_cases=test_cases, seed=seed)


_session = _HegelSession()


def hegel(
    test_fn: Callable[[], None] | None = None,
    *,
    seed: int | None = None,
    test_cases: int = 100,
) -> Callable[[Callable[[], None]], Callable[[], None]] | Callable[[], None]:
    """Decorator for running property-based tests with Hegel.

    Usage:

        @hegel
        def test_addition_commutative():
            a = integers().generate()
            b = integers().generate()
            assert a + b == b + a

        @hegel(test_cases=500)
        def test_list_reverse():
            xs = lists(integers()).generate()
            assert list(reversed(list(reversed(xs)))) == xs
    """

    def decorator(fn: Callable[[], None]) -> Callable[[], None]:
        @functools.wraps(fn)
        def wrapper() -> None:
            run_hegel_test(fn, test_cases=test_cases, seed=seed)

        return wrapper

    if test_fn is not None:
        return decorator(test_fn)

    return decorator


def run_hegel_test(
    test_fn: Callable[[], None],
    *,
    seed: int | None,
    test_cases: int = 100,
) -> None:
    """Run a property test using the shared hegeld process.

    If the test fails:
    - Re-raises the original exception if there's exactly one minimal failing case
    - Raises an ExceptionGroup if there are multiple distinct minimal failing cases
    """
    _session.run_test(test_fn, test_cases, seed)
