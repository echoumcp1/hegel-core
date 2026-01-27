"""
Hegel Python SDK - Reference implementation for writing property tests.

This SDK provides the API for writing property-based tests using Hegel.
Tests can run in two modes:
- Embedded mode (default): Uses @hegel decorator, automatically spawns hegeld
- Client mode: Manually connects to a running hegeld server

Example usage with @hegel decorator:

    from hegel.sdk import hegel, gen

    @hegel
    def test_addition_is_commutative():
        a = gen.integers().generate()
        b = gen.integers().generate()
        assert a + b == b + a

Example usage with generators:

    from hegel.sdk import gen

    @hegel
    def test_list_reverse():
        xs = gen.lists(gen.integers()).generate()
        assert list(reversed(list(reversed(xs)))) == xs
"""

import functools
import os
import socket
import subprocess
import sys
import tempfile
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from enum import Enum
from typing import Any, TypeVar

import cbor2

from hegel.protocol import (
    VERSION_NEGOTIATION_MESSAGE,
    VERSION_NEGOTIATION_OK,
    Channel,
    Connection,
    RequestError,
)

# Context variables for the current test case
_current_channel: ContextVar[Channel | None] = ContextVar(
    "_current_channel", default=None
)
_is_final: ContextVar[bool] = ContextVar("_is_final", default=False)


class AssumeRejected(Exception):
    """Raised when assume() condition is False."""

    pass


class OverflowError(Exception):
    """Raised when the server runs out of data."""

    pass


class Verbosity(Enum):
    """Verbosity level for test output."""

    QUIET = "quiet"
    NORMAL = "normal"
    VERBOSE = "verbose"
    DEBUG = "debug"


@dataclass
class TestResult:
    """Result of running a property test."""

    passed: bool
    examples_run: int
    valid_examples: int
    invalid_examples: int
    failure: dict | None = None


class Client:
    """Client for connecting to a Hegel server."""

    def __init__(self, connection: Connection):
        id = connection.control_channel.send_request(VERSION_NEGOTIATION_MESSAGE)
        response = connection.control_channel.receive_response(id)
        if response != VERSION_NEGOTIATION_OK:
            raise ConnectionError(f"Bad handshake result {response!r}")

        self.connection = connection
        self._control = connection.control_channel

    def run_test(
        self,
        name: str,
        test_fn: Callable[[], None],
        test_cases: int = 1000,
    ) -> TestResult:
        """Run a property test.

        Args:
            name: Name of the test (used for database key)
            test_fn: The test function to run. Should use generate(), assume(), etc.
            test_cases: Maximum number of test cases to run

        Returns:
            TestResult with pass/fail status and statistics
        """
        # Send run_test request
        pending = self._control.request(
            {
                "command": "run_test",
                "name": name,
                "test_cases": test_cases,
            }
        )

        # Handle test_case events until test_done
        while True:
            req_id, payload = self._control.receive_request()
            message = cbor2.loads(payload)

            event = message.get("event")

            if event == "test_case":
                channel_id = message["channel"]
                is_final = message.get("is_final", False)

                # Connect to the test channel
                test_channel = self.connection.connect_channel(channel_id)

                # Run the test function
                status, origin = self._run_test_case(test_channel, test_fn, is_final)

                # Send mark_complete
                test_channel.request(
                    {
                        "command": "mark_complete",
                        "status": status,
                        "origin": origin,
                    }
                ).get()

                # Clean up
                test_channel.close()

                # Acknowledge the test_case request
                self._control.send_response(req_id, cbor2.dumps({"result": None}))

            elif event == "test_done":
                # Acknowledge and break
                self._control.send_response(req_id, cbor2.dumps({"result": None}))
                break

            else:
                # Unknown event - acknowledge anyway
                self._control.send_response(req_id, cbor2.dumps({"result": None}))

        # Get the final result from run_test response
        result_data = pending.get()

        return TestResult(
            passed=result_data.get("passed", True),
            examples_run=result_data.get("examples_run", 0),
            valid_examples=result_data.get("valid_examples", 0),
            invalid_examples=result_data.get("invalid_examples", 0),
            failure=result_data.get("failure"),
        )

    def _run_test_case(
        self,
        channel: Channel,
        test_fn: Callable[[], None],
        is_final: bool,
    ) -> tuple[str, dict | None]:
        """Run a single test case.

        Returns (status, origin) tuple.
        """
        # Set context variables
        token_channel = _current_channel.set(channel)
        token_final = _is_final.set(is_final)

        try:
            test_fn()
            return ("VALID", None)

        except AssumeRejected:
            return ("INVALID", None)

        except OverflowError:
            # Server ran out of data - treat as invalid
            return ("INVALID", None)

        except Exception as e:
            # Extract origin from exception
            tb = e.__traceback__
            origin = _extract_origin(e, tb)
            return ("INTERESTING", origin)

        finally:
            _current_channel.reset(token_channel)
            _is_final.reset(token_final)


def _extract_origin(exc: Exception, tb: Any) -> dict:
    """Extract InterestingOrigin from an exception."""
    # Walk the traceback to find the user's test code
    # (skip frames from this SDK)
    filename = ""
    lineno = 0

    if tb is not None:
        # Get the last frame from the traceback
        while tb.tb_next is not None:
            tb = tb.tb_next
        filename = tb.tb_frame.f_code.co_filename
        lineno = tb.tb_lineno

    return {
        "exc_type": type(exc).__name__,
        "filename": filename,
        "lineno": lineno,
    }


def _get_channel() -> Channel:
    """Get the current test channel, raising if not in a test."""
    channel = _current_channel.get()
    if channel is None:
        raise RuntimeError(
            "Not in a test context - must be called from within a test function"
        )
    return channel


# =============================================================================
# Core API functions
# =============================================================================


def generate_from_schema(schema: dict) -> Any:
    """Generate a value from a schema.

    This is the low-level generation function. Prefer using Generator classes
    for a more ergonomic API.

    Args:
        schema: JSON-like schema describing the value to generate.
                Examples:
                - {"type": "integer", "minimum": 0, "maximum": 100}
                - {"type": "string", "min_size": 1}
                - {"type": "list", "elements": {"type": "integer"}}

    Returns:
        A generated value matching the schema.

    Raises:
        OverflowError: If the server runs out of data (test case will be rejected)
        RuntimeError: If called outside a test context
    """
    channel = _get_channel()
    try:
        return channel.request({"command": "generate", "schema": schema}).get()
    except RequestError as e:
        if e.error_type == "StopTest":
            raise OverflowError("Server ran out of data") from e
        raise


# Alias for backwards compatibility
draw = generate_from_schema


def assume(condition: bool) -> None:
    """Reject the current test case if condition is False.

    Use this to filter out invalid inputs. Hegel will generate new inputs
    rather than counting this as a failure.

    Args:
        condition: If False, the test case is rejected.
    """
    if not condition:
        raise AssumeRejected


def note(message: str) -> None:
    """Record a message that will be printed on the final (failing) run.

    Use this to debug failing tests by printing intermediate values.
    Notes are only printed when replaying a failure, not during normal
    test execution.

    Args:
        message: The message to record.
    """
    if _is_final.get():
        print(message, file=sys.stderr)


def target(value: float, label: str = "") -> None:
    """Guide the search toward higher values.

    Hegel will try to find inputs that maximize the target value.
    This can help find edge cases more quickly.

    Args:
        value: A numeric value to maximize.
        label: Optional label for this target (useful if targeting multiple values).
    """
    channel = _get_channel()
    channel.request({"command": "target", "value": value, "label": label}).get()


def start_span(label: int = 0) -> None:
    """Start a generation span for better shrinking.

    Spans help Hegel understand the structure of generated data,
    which improves shrinking. Values generated within a span can be
    shrunk together.

    Args:
        label: Optional label for the span.
    """
    channel = _get_channel()
    channel.request({"command": "start_span", "label": label}).get()


def stop_span(*, discard: bool = False) -> None:
    """End the current generation span.

    Args:
        discard: If True, mark the span as discarded (e.g., because the
                 generated values didn't pass a filter).
    """
    channel = _get_channel()
    channel.request({"command": "stop_span", "discard": discard}).get()


# =============================================================================
# Generator classes (following Rust SDK naming conventions)
# =============================================================================


@dataclass
class Generator:
    """A generator for producing test values.

    Generators describe how to produce random values for property tests.
    Use the .generate() method to get a value during test execution.
    """

    schema: dict

    def generate(self) -> Any:
        """Generate a value from this generator."""
        return generate_from_schema(self.schema)

    # Alias for backwards compatibility with Hypothesis-style naming
    def draw(self) -> Any:
        """Generate a value from this generator.

        This is an alias for .generate() for backwards compatibility.
        """
        return self.generate()


# For backwards compatibility
Strategy = Generator


class gen:
    """Namespace for generator factories.

    All generators are accessed through this namespace, following the pattern
    from the Rust SDK:

        from hegel.sdk import gen

        x = gen.integers().generate()
        s = gen.text().generate()
        xs = gen.lists(gen.integers()).generate()
    """

    @staticmethod
    def integers(
        min_value: int | None = None, max_value: int | None = None
    ) -> Generator:
        """Generator for integers.

        Args:
            min_value: Minimum value (inclusive), or None for unbounded.
            max_value: Maximum value (inclusive), or None for unbounded.
        """
        schema: dict = {"type": "integer"}
        if min_value is not None:
            schema["minimum"] = min_value
        if max_value is not None:
            schema["maximum"] = max_value
        return Generator(schema)

    @staticmethod
    def floats(
        min_value: float | None = None,
        max_value: float | None = None,
        *,
        allow_nan: bool = False,
        allow_infinity: bool = False,
    ) -> Generator:
        """Generator for floating-point numbers.

        Args:
            min_value: Minimum value (inclusive), or None for unbounded.
            max_value: Maximum value (inclusive), or None for unbounded.
            allow_nan: Whether to allow NaN values.
            allow_infinity: Whether to allow infinite values.
        """
        schema: dict = {"type": "number"}
        if min_value is not None:
            schema["minimum"] = min_value
        if max_value is not None:
            schema["maximum"] = max_value
        schema["allow_nan"] = allow_nan
        schema["allow_infinity"] = allow_infinity
        return Generator(schema)

    @staticmethod
    def booleans(p: float = 0.5) -> Generator:
        """Generator for booleans.

        Args:
            p: Probability of True.
        """
        return Generator({"type": "boolean", "p": p})

    @staticmethod
    def text(min_size: int = 0, max_size: int | None = None) -> Generator:
        """Generator for text strings.

        Args:
            min_size: Minimum length.
            max_size: Maximum length, or None for unbounded.
        """
        schema: dict = {"type": "string", "min_size": min_size}
        if max_size is not None:
            schema["max_size"] = max_size
        return Generator(schema)

    @staticmethod
    def binary(min_size: int = 0, max_size: int | None = None) -> Generator:
        """Generator for binary data (returned as base64).

        Args:
            min_size: Minimum length in bytes.
            max_size: Maximum length in bytes, or None for unbounded.
        """
        schema: dict = {"type": "binary", "min_size": min_size}
        if max_size is not None:
            schema["max_size"] = max_size
        return Generator(schema)

    @staticmethod
    def lists(
        elements: Generator | dict,
        min_size: int = 0,
        max_size: int | None = None,
    ) -> Generator:
        """Generator for lists.

        Args:
            elements: Generator or schema for list elements.
            min_size: Minimum length.
            max_size: Maximum length, or None for unbounded.
        """
        elem_schema = elements.schema if isinstance(elements, Generator) else elements
        schema: dict = {"type": "list", "elements": elem_schema, "min_size": min_size}
        if max_size is not None:
            schema["max_size"] = max_size
        return Generator(schema)

    # Rust-style alias
    vecs = lists

    @staticmethod
    def tuples(*elements: Generator | dict) -> Generator:
        """Generator for tuples.

        Args:
            *elements: Generators or schemas for each tuple element.
        """
        elem_schemas = [e.schema if isinstance(e, Generator) else e for e in elements]
        return Generator({"type": "tuple", "elements": elem_schemas})

    @staticmethod
    def just(value: Any) -> Generator:
        """Generator that always returns the same value.

        Args:
            value: The constant value to return.
        """
        return Generator({"const": value})

    @staticmethod
    def sampled_from(values: list) -> Generator:
        """Generator that samples from a list of values.

        Args:
            values: The values to sample from.
        """
        return Generator({"sampled_from": values})

    @staticmethod
    def one_of(*generators: Generator | dict) -> Generator:
        """Generator that picks from one of several generators.

        Args:
            *generators: Generators or schemas to choose from.
        """
        schemas = [g.schema if isinstance(g, Generator) else g for g in generators]
        return Generator({"one_of": schemas})

    @staticmethod
    def optional(element: Generator | dict) -> Generator:
        """Generator for optional values (None or a value).

        Args:
            element: Generator or schema for the value when present.
        """
        elem_schema = element.schema if isinstance(element, Generator) else element
        return gen.one_of(gen.just(None), Generator(elem_schema))


# =============================================================================
# Backwards-compatible function-style generators
# =============================================================================


def integers(min_value: int | None = None, max_value: int | None = None) -> Generator:
    """Generator for integers. See gen.integers() for details."""
    return gen.integers(min_value, max_value)


def floats(
    min_value: float | None = None,
    max_value: float | None = None,
    *,
    allow_nan: bool = False,
    allow_infinity: bool = False,
) -> Generator:
    """Generator for floats. See gen.floats() for details."""
    return gen.floats(
        min_value, max_value, allow_nan=allow_nan, allow_infinity=allow_infinity
    )


def booleans(p: float = 0.5) -> Generator:
    """Generator for booleans. See gen.booleans() for details."""
    return gen.booleans(p)


def text(min_size: int = 0, max_size: int | None = None) -> Generator:
    """Generator for text strings. See gen.text() for details."""
    return gen.text(min_size, max_size)


def binary(min_size: int = 0, max_size: int | None = None) -> Generator:
    """Generator for binary data. See gen.binary() for details."""
    return gen.binary(min_size, max_size)


def lists(
    elements: Generator | dict,
    min_size: int = 0,
    max_size: int | None = None,
) -> Generator:
    """Generator for lists. See gen.lists() for details."""
    return gen.lists(elements, min_size, max_size)


def tuples(*elements: Generator | dict) -> Generator:
    """Generator for tuples. See gen.tuples() for details."""
    return gen.tuples(*elements)


def just(value: Any) -> Generator:
    """Generator for a constant value. See gen.just() for details."""
    return gen.just(value)


def sampled_from(values: list) -> Generator:
    """Generator sampling from a list. See gen.sampled_from() for details."""
    return gen.sampled_from(values)


def one_of(*generators: Generator | dict) -> Generator:
    """Generator picking from alternatives. See gen.one_of() for details."""
    return gen.one_of(*generators)


# =============================================================================
# @hegel decorator for embedded mode (auto-spawning hegeld)
# =============================================================================


F = TypeVar("F", bound=Callable[..., Any])


def _find_hegeld() -> str:
    """Find the hegeld binary path."""
    # First check if we're in a venv and hegel is installed there
    if sys.prefix != sys.base_prefix:
        # We're in a venv
        venv_hegel = os.path.join(sys.prefix, "bin", "hegel")
        if os.path.exists(venv_hegel):
            return venv_hegel

    # Try to find hegel in PATH
    import shutil

    hegel_path = shutil.which("hegel")
    if hegel_path:
        return hegel_path

    # Fall back to using python -m hegel
    return f"{sys.executable} -m hegel"


def hegel(
    test_fn: Callable[[], None] | None = None,
    *,
    test_cases: int = 100,
    verbosity: Verbosity = Verbosity.NORMAL,
) -> Callable[[Callable[[], None]], Callable[[], None]] | Callable[[], None]:
    """Decorator for running property-based tests with Hegel.

    This decorator automatically spawns a hegeld server, runs the test function
    multiple times with different generated inputs, and reports failures.

    Usage:

        @hegel
        def test_addition_commutative():
            a = gen.integers().generate()
            b = gen.integers().generate()
            assert a + b == b + a

        @hegel(test_cases=500, verbosity=Verbosity.VERBOSE)
        def test_list_reverse():
            xs = gen.lists(gen.integers()).generate()
            assert list(reversed(list(reversed(xs)))) == xs

    Args:
        test_fn: The test function to decorate.
        test_cases: Number of test cases to run (default: 100).
        verbosity: Output verbosity level.

    Returns:
        Decorated test function.
    """

    def decorator(fn: Callable[[], None]) -> Callable[[], None]:
        @functools.wraps(fn)
        def wrapper() -> None:
            run_hegel_test(fn, test_cases=test_cases, verbosity=verbosity)

        return wrapper

    if test_fn is not None:
        # Used as @hegel without parentheses
        return decorator(test_fn)

    # Used as @hegel() or @hegel(test_cases=N)
    return decorator


def run_hegel_test(
    test_fn: Callable[[], None],
    *,
    test_cases: int = 100,
    verbosity: Verbosity = Verbosity.NORMAL,
) -> TestResult:
    """Run a property test with automatic hegeld spawning.

    This is the programmatic equivalent of the @hegel decorator.

    Args:
        test_fn: The test function to run.
        test_cases: Number of test cases to run.
        verbosity: Output verbosity level.

    Returns:
        TestResult with pass/fail status and statistics.

    Raises:
        AssertionError: If the test fails.
    """
    # Create a temp directory for the socket
    with tempfile.TemporaryDirectory(prefix="hegel-") as temp_dir:
        socket_path = os.path.join(temp_dir, "hegel.sock")

        # Create server socket
        server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server_sock.bind(socket_path)
        server_sock.listen(1)

        # Spawn hegeld in client mode
        hegel_cmd = _find_hegeld()
        cmd_args = hegel_cmd.split() + [
            "--client-mode",
            socket_path,
            "--test-cases",
            str(test_cases),
            "--verbosity",
            verbosity.value,
        ]

        if verbosity in (Verbosity.VERBOSE, Verbosity.DEBUG):
            print(f"Starting hegeld: {' '.join(cmd_args)}", file=sys.stderr)

        process = subprocess.Popen(
            cmd_args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        try:
            # Accept the connection from hegeld
            client_sock, _ = server_sock.accept()

            if verbosity in (Verbosity.VERBOSE, Verbosity.DEBUG):
                print("hegeld connected", file=sys.stderr)

            # Create connection and client
            connection = Connection(client_sock, name="SDK")
            client = Client(connection)

            # Get the test name from the function
            test_name = test_fn.__name__ if hasattr(test_fn, "__name__") else "test"

            # Run the test
            result = client.run_test(test_name, test_fn, test_cases=test_cases)

            # Close connection
            connection.close()

            # Wait for hegeld to exit
            process.wait(timeout=5)

            # Handle result
            if not result.passed:
                failure = result.failure or {}
                exc_type = failure.get("exc_type", "AssertionError")
                filename = failure.get("filename", "")
                lineno = failure.get("lineno", 0)
                raise AssertionError(
                    f"Property test failed: {exc_type} at {filename}:{lineno}"
                )

            return result

        except Exception:
            # Make sure to clean up the process
            process.terminate()
            process.wait(timeout=5)
            raise

        finally:
            server_sock.close()


# =============================================================================
# Exports for backwards compatibility
# =============================================================================

__all__ = [
    "AssumeRejected",
    "Client",
    "Generator",
    "OverflowError",
    "Strategy",
    "TestResult",
    "Verbosity",
    "assume",
    "binary",
    "booleans",
    "draw",
    "floats",
    "gen",
    "generate_from_schema",
    "hegel",
    "integers",
    "just",
    "lists",
    "note",
    "one_of",
    "run_hegel_test",
    "sampled_from",
    "start_span",
    "stop_span",
    "target",
    "text",
    "tuples",
]
