"""
Hegel Python SDK - Reference implementation for writing property tests.

This SDK provides the API for writing property-based tests using Hegel.

Example usage:

    from hegel.sdk import hegel, integers, lists

    @hegel
    def test_addition_is_commutative():
        a = integers().generate()
        b = integers().generate()
        assert a + b == b + a
"""

import functools
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from abc import ABC, abstractmethod
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


# Span labels (matching Rust SDK)
class Labels:
    LIST = 1
    LIST_ELEMENT = 2
    SET = 3
    SET_ELEMENT = 4
    MAP = 5
    MAP_ENTRY = 6
    TUPLE = 7
    ONE_OF = 8
    OPTIONAL = 9
    FIXED_DICT = 10
    FLAT_MAP = 11
    FILTER = 12


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
        """Run a property test."""
        pending = self._control.request(
            {
                "command": "run_test",
                "name": name,
                "test_cases": test_cases,
            }
        )

        while True:
            req_id, payload = self._control.receive_request()
            message = cbor2.loads(payload)

            event = message.get("event")

            if event == "test_case":
                channel_id = message["channel"]
                is_final = message.get("is_final", False)

                test_channel = self.connection.connect_channel(channel_id)
                status, origin = self._run_test_case(test_channel, test_fn, is_final)

                test_channel.request(
                    {
                        "command": "mark_complete",
                        "status": status,
                        "origin": origin,
                    }
                ).get()

                test_channel.close()
                self._control.send_response(req_id, cbor2.dumps({"result": None}))

            elif event == "test_done":
                self._control.send_response(req_id, cbor2.dumps({"result": None}))
                break

            else:
                self._control.send_response(req_id, cbor2.dumps({"result": None}))

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
        """Run a single test case."""
        token_channel = _current_channel.set(channel)
        token_final = _is_final.set(is_final)

        try:
            test_fn()
            return ("VALID", None)

        except AssumeRejected:
            return ("INVALID", None)

        except OverflowError:
            return ("INVALID", None)

        except Exception as e:
            tb = e.__traceback__
            origin = _extract_origin(e, tb)
            return ("INTERESTING", origin)

        finally:
            _current_channel.reset(token_channel)
            _is_final.reset(token_final)


def _extract_origin(exc: Exception, tb: Any) -> dict:
    """Extract InterestingOrigin from an exception."""
    filename = ""
    lineno = 0

    if tb is not None:
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
    """Generate a value from a schema."""
    channel = _get_channel()
    try:
        return channel.request({"command": "generate", "schema": schema}).get()
    except RequestError as e:
        if e.error_type == "StopTest":
            raise OverflowError("Server ran out of data") from e
        raise


def assume(condition: bool) -> None:
    """Reject the current test case if condition is False."""
    if not condition:
        raise AssumeRejected


def note(message: str) -> None:
    """Record a message that will be printed on the final (failing) run."""
    if _is_final.get():
        print(message, file=sys.stderr)


def target(value: float, label: str = "") -> None:
    """Guide the search toward higher values."""
    channel = _get_channel()
    channel.request({"command": "target", "value": value, "label": label}).get()


def start_span(label: int = 0) -> None:
    """Start a generation span for better shrinking."""
    channel = _get_channel()
    channel.request({"command": "start_span", "label": label}).get()


def stop_span(*, discard: bool = False) -> None:
    """End the current generation span."""
    channel = _get_channel()
    channel.request({"command": "stop_span", "discard": discard}).get()


# =============================================================================
# Generator base class and combinators
# =============================================================================

T = TypeVar("T")
U = TypeVar("U")


class Generator(ABC):
    """Base class for all generators.

    Generators produce values of type T and optionally carry a schema
    that describes the values they generate. Generators with a schema
    can be optimized into a single server request.
    """

    @abstractmethod
    def generate(self) -> Any:
        """Generate a value."""
        pass

    def schema(self) -> dict | None:
        """Get the schema for this generator, if available.

        Schemas enable composition optimizations where a single request
        to the server can generate complex nested structures.

        Returns None for composite generators (map, filter, flat_map).
        """
        return None

    def map(self, f: Callable[[Any], Any]) -> "MappedGenerator":
        """Transform generated values using a function.

        The resulting generator has no schema since the transformation
        may invalidate the schema's semantics.
        """
        return MappedGenerator(self, f)

    def flat_map(self, f: Callable[[Any], "Generator"]) -> "FlatMappedGenerator":
        """Generate a value, then use it to create another generator.

        This is useful for dependent generation where the second value
        depends on the first.
        """
        return FlatMappedGenerator(self, f)

    def filter(
        self, predicate: Callable[[Any], bool], max_attempts: int = 100
    ) -> "FilteredGenerator":
        """Filter generated values using a predicate.

        If max_attempts consecutive values fail the predicate, calls assume(false).
        """
        return FilteredGenerator(self, predicate, max_attempts)


class SchemaGenerator(Generator):
    """A generator backed by a JSON schema."""

    def __init__(self, schema_dict: dict):
        self._schema = schema_dict

    def generate(self) -> Any:
        """Generate a value from the schema."""
        return generate_from_schema(self._schema)

    def schema(self) -> dict | None:
        return self._schema


class MappedGenerator(Generator):
    """A generator that transforms values from another generator."""

    def __init__(self, source: Generator, f: Callable[[Any], Any]):
        self._source = source
        self._f = f

    def generate(self) -> Any:
        start_span(Labels.MAP)
        try:
            value = self._source.generate()
            return self._f(value)
        finally:
            stop_span(discard=False)

    def schema(self) -> dict | None:
        return None  # No schema after transformation


class FlatMappedGenerator(Generator):
    """A generator for dependent generation."""

    def __init__(self, source: Generator, f: Callable[[Any], Generator]):
        self._source = source
        self._f = f

    def generate(self) -> Any:
        start_span(Labels.FLAT_MAP)
        try:
            first = self._source.generate()
            second_gen = self._f(first)
            return second_gen.generate()
        finally:
            stop_span(discard=False)

    def schema(self) -> dict | None:
        return None  # No schema for dependent generation


class FilteredGenerator(Generator):
    """A generator that filters values."""

    def __init__(
        self, source: Generator, predicate: Callable[[Any], bool], max_attempts: int
    ):
        self._source = source
        self._predicate = predicate
        self._max_attempts = max_attempts

    def generate(self) -> Any:
        for _ in range(self._max_attempts):
            start_span(Labels.FILTER)
            value = self._source.generate()
            if self._predicate(value):
                stop_span(discard=False)
                return value
            stop_span(discard=True)
        # Too many failed attempts - reject this test case
        assume(False)
        raise AssertionError("unreachable")

    def schema(self) -> dict | None:
        return None  # No schema after filtering


# =============================================================================
# Generator factory functions
# =============================================================================


def integers(min_value: int | None = None, max_value: int | None = None) -> Generator:
    """Generator for integers."""
    schema: dict = {"type": "integer"}
    if min_value is not None:
        schema["minimum"] = min_value
    if max_value is not None:
        schema["maximum"] = max_value
    return SchemaGenerator(schema)


def floats(
    min_value: float | None = None,
    max_value: float | None = None,
    *,
    allow_nan: bool = False,
    allow_infinity: bool = False,
) -> Generator:
    """Generator for floating-point numbers."""
    schema: dict = {"type": "number"}
    if min_value is not None:
        schema["minimum"] = min_value
    if max_value is not None:
        schema["maximum"] = max_value
    schema["allow_nan"] = allow_nan
    schema["allow_infinity"] = allow_infinity
    return SchemaGenerator(schema)


def booleans(p: float = 0.5) -> Generator:
    """Generator for booleans."""
    return SchemaGenerator({"type": "boolean", "p": p})


def text(min_size: int = 0, max_size: int | None = None) -> Generator:
    """Generator for text strings."""
    schema: dict = {"type": "string", "min_size": min_size}
    if max_size is not None:
        schema["max_size"] = max_size
    return SchemaGenerator(schema)


def binary(min_size: int = 0, max_size: int | None = None) -> Generator:
    """Generator for binary data (returned as base64)."""
    schema: dict = {"type": "binary", "min_size": min_size}
    if max_size is not None:
        schema["max_size"] = max_size
    return SchemaGenerator(schema)


def lists(
    elements: Generator,
    min_size: int = 0,
    max_size: int | None = None,
) -> Generator:
    """Generator for lists."""
    elem_schema = elements.schema()
    if elem_schema is not None:
        # Can compose into single schema
        schema: dict = {"type": "list", "elements": elem_schema, "min_size": min_size}
        if max_size is not None:
            schema["max_size"] = max_size
        return SchemaGenerator(schema)
    else:
        # Composite generator - must generate element by element
        return CompositeListGenerator(elements, min_size, max_size)


class CompositeListGenerator(Generator):
    """A list generator for elements without a schema."""

    def __init__(
        self, elements: Generator, min_size: int, max_size: int | None
    ):
        self._elements = elements
        self._min_size = min_size
        self._max_size = max_size

    def generate(self) -> list:
        start_span(Labels.LIST)
        try:
            # First get the size
            size_schema: dict = {"type": "integer", "minimum": self._min_size}
            if self._max_size is not None:
                size_schema["maximum"] = self._max_size
            else:
                size_schema["maximum"] = self._min_size + 10  # reasonable default

            size = generate_from_schema(size_schema)
            result = []
            for _ in range(size):
                start_span(Labels.LIST_ELEMENT)
                result.append(self._elements.generate())
                stop_span(discard=False)
            return result
        finally:
            stop_span(discard=False)


def tuples(*elements: Generator) -> Generator:
    """Generator for tuples."""
    # Check if all elements have schemas
    schemas = [e.schema() for e in elements]
    if all(s is not None for s in schemas):
        return SchemaGenerator({"type": "tuple", "elements": schemas})
    else:
        return CompositeTupleGenerator(list(elements))


class CompositeTupleGenerator(Generator):
    """A tuple generator for elements without schemas."""

    def __init__(self, elements: list[Generator]):
        self._elements = elements

    def generate(self) -> tuple:
        start_span(Labels.TUPLE)
        try:
            result = []
            for elem in self._elements:
                result.append(elem.generate())
            return tuple(result)
        finally:
            stop_span(discard=False)


def just(value: Any) -> Generator:
    """Generator that always returns the same value."""
    return SchemaGenerator({"const": value})


def sampled_from(values: list) -> Generator:
    """Generator that samples from a list of values."""
    return SchemaGenerator({"sampled_from": values})


def one_of(*generators: Generator) -> Generator:
    """Generator that picks from one of several generators."""
    # Check if all generators have schemas
    schemas = [g.schema() for g in generators]
    if all(s is not None for s in schemas):
        return SchemaGenerator({"one_of": schemas})
    else:
        return CompositeOneOfGenerator(list(generators))


class CompositeOneOfGenerator(Generator):
    """A one_of generator for generators without schemas."""

    def __init__(self, generators: list[Generator]):
        self._generators = generators

    def generate(self) -> Any:
        start_span(Labels.ONE_OF)
        try:
            # Pick which generator to use
            index = generate_from_schema(
                {"type": "integer", "minimum": 0, "maximum": len(self._generators) - 1}
            )
            return self._generators[index].generate()
        finally:
            stop_span(discard=False)


def optional(element: Generator) -> Generator:
    """Generator for optional values (None or a value)."""
    return one_of(just(None), element)


# =============================================================================
# @hegel decorator
# =============================================================================


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


def hegel(
    test_fn: Callable[[], None] | None = None,
    *,
    test_cases: int = 100,
    verbosity: Verbosity = Verbosity.NORMAL,
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
            run_hegel_test(fn, test_cases=test_cases, verbosity=verbosity)

        return wrapper

    if test_fn is not None:
        return decorator(test_fn)

    return decorator


def run_hegel_test(
    test_fn: Callable[[], None],
    *,
    test_cases: int = 100,
    verbosity: Verbosity = Verbosity.NORMAL,
) -> TestResult:
    """Run a property test with automatic hegeld spawning."""
    with tempfile.TemporaryDirectory(prefix="hegel-") as temp_dir:
        socket_path = os.path.join(temp_dir, "hegel.sock")

        server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server_sock.bind(socket_path)
        server_sock.listen(1)

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
            client_sock, _ = server_sock.accept()

            if verbosity in (Verbosity.VERBOSE, Verbosity.DEBUG):
                print("hegeld connected", file=sys.stderr)

            connection = Connection(client_sock, name="SDK")
            client = Client(connection)

            test_name = test_fn.__name__ if hasattr(test_fn, "__name__") else "test"
            result = client.run_test(test_name, test_fn, test_cases=test_cases)

            connection.close()
            process.wait(timeout=5)

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
            process.terminate()
            process.wait(timeout=5)
            raise

        finally:
            server_sock.close()
