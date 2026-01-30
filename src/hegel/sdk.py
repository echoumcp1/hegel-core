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
import types
from abc import ABC, abstractmethod
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import fields, is_dataclass
from enum import Enum
from typing import Any, TypeVar, Union, get_args, get_origin

import cbor2

from hegel.protocol import (
    Channel,
    Connection,
    RequestError,
)

# Context variables for the current test case
_current_channel: ContextVar[Channel | None] = ContextVar(
    "_current_channel",
    default=None,
)
_is_final: ContextVar[bool] = ContextVar("_is_final", default=False)
_test_aborted: ContextVar[bool] = ContextVar("_test_aborted", default=False)


class AssumeRejected(Exception):
    """Raised when assume() condition is False."""


class DataExhausted(Exception):
    """Raised when the server runs out of test data (StopTest)."""


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
    MAPPED = 13  # For .map() transformations
    SAMPLED_FROM = 14


class Client:
    """Client for connecting to a Hegel server."""

    def __init__(self, connection: Connection):
        connection.send_handshake()

        self.connection = connection
        self._control = connection.control_channel
        self.__lock = threading.Lock()

    def run_test(
        self,
        name: str,
        test_fn: Callable[[], None],
        test_cases: int = 1000,
    ) -> None:
        """Run a property test."""

        test_channel = self.connection.new_channel(role="Test")

        # Channels aren't thread safe, so we've got to request starting a thread
        # under a lock.
        with self.__lock:
            self._control.request(
                {
                    "command": "run_test",
                    "name": name,
                    "test_cases": test_cases,
                    "channel": test_channel.channel_id,
                },
            ).get()

        result_data = None

        test_case_count = 0
        while True:
            message_id, message = test_channel.receive_request()
            test_case_count += 1
            event = message.get("event")

            if event == "test_case":
                channel_id = message["channel"]
                test_channel.send_response_value(message_id, None)
                test_case_channel = self.connection.connect_channel(
                    channel_id,
                    role="Test Case",
                )
                self._run_test_case(test_case_channel, test_fn, is_final=False)
            elif event == "test_done":
                test_channel.send_response_value(message_id, message=True)
                result_data = message["results"]
                break
            else:
                test_channel.send_response_raw(
                    message_id,
                    cbor2.dumps(
                        {
                            "error": f"Unrecognised event {event}",
                            "type": "InvalidMessage",
                        },
                    ),
                )

        assert result_data is not None

        n_interesting = result_data["interesting_test_cases"]

        if n_interesting == 0:
            return
        exceptions: list[Exception] = []
        for i in range(n_interesting):
            try:
                message_id, message = test_channel.receive_request()
                test_case_count += 1
                assert message["event"] == "test_case"

                channel_id = message["channel"]
                test_channel.send_response_value(message_id, None)
                test_case_channel = self.connection.connect_channel(
                    channel_id,
                    role="Test Case",
                )
                self._run_test_case(test_case_channel, test_fn, is_final=True)
                if n_interesting > 1:
                    raise AssertionError(
                        f"Expected test case {i} to fail but it didn't",
                    )
                else:
                    raise AssertionError("Expected test case to fail but it didn't")
            except Exception as e:
                if n_interesting == 1:
                    raise
                exceptions.append(e)
        raise ExceptionGroup("multiple failures", exceptions)

    def _run_test_case(
        self,
        channel: Channel,
        test_fn: Callable[[], None],
        *,
        is_final: bool,
    ) -> None:
        """Run a single test case."""
        token_channel = _current_channel.set(channel)
        token_final = _is_final.set(is_final)
        token_aborted = _test_aborted.set(False)
        already_complete = False
        status = "VALID"
        origin = None
        try:
            test_fn()
        except AssumeRejected:
            status = "INVALID"
        except DataExhausted:
            # Server ran out of data - already marked complete server side.
            already_complete = True
        except ConnectionError:
            raise
        except Exception as e:
            status = "INTERESTING"
            tb = e.__traceback__
            origin = _extract_origin(e, tb)
            if is_final:
                raise
        finally:
            _current_channel.reset(token_channel)
            _is_final.reset(token_final)
            _test_aborted.reset(token_aborted)
            if not already_complete:
                channel.send_request(
                    {
                        "command": "mark_complete",
                        "status": status,
                        "origin": origin,
                    },
                )
            channel.close()


def _extract_origin(exc: Exception, tb: Any) -> str:
    """Extract InterestingOrigin from an exception."""
    filename = ""
    lineno = 0

    if tb is not None:
        while tb.tb_next is not None:
            tb = tb.tb_next
        filename = tb.tb_frame.f_code.co_filename
        lineno = tb.tb_lineno

    return f"{type(exc).__name__} at {filename}:{lineno}"


def _get_channel() -> Channel:
    """Get the current test channel, raising if not in a test."""
    channel = _current_channel.get()
    if channel is None:
        raise RuntimeError(
            "Not in a test context - must be called from within a test function",
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
            # Mark that this test case has been aborted - server won't respond
            # to any more messages on this channel
            _test_aborted.set(True)
            raise DataExhausted("Server ran out of data") from e
        raise


def assume(condition: bool) -> None:  # noqa: FBT001
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
    # If test was aborted (StopTest), don't try to communicate with server
    if _test_aborted.get():
        return
    channel = _get_channel()
    channel.request({"command": "start_span", "label": label}).get()


def stop_span(*, discard: bool = False) -> None:
    """End the current generation span.

    If the server has signaled StopTest (DataExhausted), this is a no-op
    since the server has already abandoned this test case.
    """
    # If test was aborted (StopTest), don't try to communicate with server
    if _test_aborted.get():
        return
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
        self,
        predicate: Callable[[Any], bool],
        max_attempts: int = 100,
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
        start_span(Labels.MAPPED)
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
        self,
        source: Generator,
        predicate: Callable[[Any], bool],
        max_attempts: int,
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
        assume(condition=False)
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

    def __init__(self, elements: Generator, min_size: int, max_size: int | None):
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


class SampledFromGenerator(Generator):
    """Generator that samples from a list of values with identity preservation.

    This generator works in two modes:
    1. If all elements are JSON primitives (None, bool, int, float, str),
       uses the schema-based approach for efficient generation.
    2. Otherwise, falls back to generating an index and returning the
       original object, preserving object identity.
    """

    def __init__(self, elements: list[Any]):
        self._elements = list(elements)
        self._json_values: list[Any] | None = None

    def schema(self) -> dict | None:
        """Return schema only if all elements are JSON primitives."""
        try:
            json_values: list[Any] = []
            for elem in self._elements:
                # Try to serialize - only primitives allowed
                if elem is None:
                    json_values.append(None)
                elif isinstance(elem, (bool, int, float, str)):
                    json_values.append(elem)
                else:
                    # Not a primitive - fallback mode
                    return None
            self._json_values = json_values
            return {"sampled_from": json_values}
        except (TypeError, ValueError):
            return None

    def generate(self) -> Any:
        schema = self.schema()
        if schema is not None:
            # Mode 1: Use schema, find matching element
            wire_value = generate_from_schema(schema)
            # Find the original element with matching JSON value
            assert self._json_values is not None  # Guaranteed after schema() succeeds
            for i, json_val in enumerate(self._json_values):
                if json_val == wire_value:
                    return self._elements[i]
            raise RuntimeError(f"Server returned {wire_value!r} not in elements")
        else:
            # Mode 2: Compositional fallback with index
            start_span(Labels.SAMPLED_FROM)
            try:
                idx = generate_from_schema(
                    {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": len(self._elements) - 1,
                    },
                )
                return self._elements[idx]
            finally:
                stop_span()


def sampled_from(values: list) -> Generator:
    """Generator that samples uniformly from a list of values.

    Works with any type, including non-JSON-serializable objects.
    For non-primitive types, returns the original objects (identity preserved).
    """
    return SampledFromGenerator(values)


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
                {"type": "integer", "minimum": 0, "maximum": len(self._generators) - 1},
            )
            return self._generators[index].generate()
        finally:
            stop_span(discard=False)


def optional(element: Generator) -> Generator:
    """Generator for optional values (None or a value)."""
    return one_of(just(None), element)


def dicts(
    keys: Generator,
    values: Generator,
    min_size: int = 0,
    max_size: int | None = None,
) -> Generator:
    """Generator for dictionaries."""
    key_schema = keys.schema()
    value_schema = values.schema()

    if key_schema is not None and value_schema is not None:
        schema: dict = {
            "type": "dict",
            "keys": key_schema,
            "values": value_schema,
            "min_size": min_size,
        }
        if max_size is not None:
            schema["max_size"] = max_size
        return SchemaDictGenerator(schema)
    else:
        return CompositeDictGenerator(keys, values, min_size, max_size)


class SchemaDictGenerator(Generator):
    """A dict generator backed by a schema.

    The server returns dicts as list of [key, value] pairs,
    so we need to convert back to a dict.
    """

    def __init__(self, schema_dict: dict):
        self._schema = schema_dict

    def generate(self) -> dict:
        # Server returns list of [key, value] pairs
        items = generate_from_schema(self._schema)
        return dict(items)

    def schema(self) -> dict | None:
        return self._schema


class CompositeDictGenerator(Generator):
    """A dict generator for elements without schemas."""

    def __init__(
        self,
        keys: Generator,
        values: Generator,
        min_size: int,
        max_size: int | None,
    ):
        self._keys = keys
        self._values = values
        self._min_size = min_size
        self._max_size = max_size

    def generate(self) -> dict:
        start_span(Labels.MAP)
        try:
            max_sz = (
                self._max_size if self._max_size is not None else self._min_size + 10
            )
            size = generate_from_schema(
                {"type": "integer", "minimum": self._min_size, "maximum": max_sz},
            )
            result = {}
            for _ in range(size):
                start_span(Labels.MAP_ENTRY)
                key = self._keys.generate()
                value = self._values.generate()
                result[key] = value
                stop_span()
            return result
        finally:
            stop_span()


# =============================================================================
# from_type() function for generating values from type hints
# =============================================================================


class DataclassGenerator(Generator):
    """Generator for dataclass instances."""

    def __init__(self, dataclass_type: type):
        if not is_dataclass(dataclass_type):
            raise TypeError(f"{dataclass_type} is not a dataclass")
        self._type = dataclass_type
        self._field_generators: dict[str, Generator] = {}

        # Create generators for each field
        for field in fields(dataclass_type):
            self._field_generators[field.name] = from_type(field.type)

    def with_field(self, field_name: str, gen: Generator) -> "DataclassGenerator":
        """Override the generator for a specific field."""
        if field_name not in self._field_generators:
            raise ValueError(f"Unknown field: {field_name}")
        # Create a copy with the modified generator
        new_gen = DataclassGenerator.__new__(DataclassGenerator)
        new_gen._type = self._type
        new_gen._field_generators = dict(self._field_generators)
        new_gen._field_generators[field_name] = gen
        return new_gen

    def schema(self) -> dict | None:
        """Return schema if all fields have schemas."""
        properties = {}
        required = []

        for field in fields(self._type):
            gen = self._field_generators[field.name]
            field_schema = gen.schema()
            if field_schema is None:
                return None  # Compositional fallback
            properties[field.name] = field_schema
            required.append(field.name)

        return {"type": "object", "properties": properties, "required": required}

    def generate(self) -> Any:
        """Generate a dataclass instance."""
        schema = self.schema()
        if schema is not None:
            # Single server request
            data = generate_from_schema(schema)
            return self._type(**data)
        else:
            # Compositional fallback
            start_span(Labels.FIXED_DICT)
            try:
                kwargs = {}
                for field in fields(self._type):
                    kwargs[field.name] = self._field_generators[field.name].generate()
                return self._type(**kwargs)
            finally:
                stop_span()


def from_type(type_hint: Any) -> Generator:
    """Generate values matching the given type hint.

    Supports:
    - Primitive types: int, float, str, bool, type(None)
    - Container types: list, dict, tuple, set
    - Optional[T] and Union[T, None]
    - Dataclasses
    - Enums
    """
    # Handle None type
    if type_hint is type(None):
        return just(None)

    # Primitives
    if type_hint is int:
        return integers()
    if type_hint is float:
        return floats()
    if type_hint is str:
        return text()
    if type_hint is bool:
        return booleans()
    if type_hint is bytes:
        return binary()

    # Get origin for generic types
    origin = get_origin(type_hint)
    args = get_args(type_hint)

    # Optional[T] is Union[T, None] or T | None (types.UnionType in Python 3.10+)
    if origin is Union or isinstance(type_hint, types.UnionType):
        # Filter out NoneType
        non_none_args = [a for a in args if a is not type(None)]
        if len(non_none_args) == 1 and type(None) in args:
            # This is Optional[T]
            return optional(from_type(non_none_args[0]))
        else:
            # General Union
            return one_of(*[from_type(a) for a in args])

    # List[T]
    if origin is list:
        if args:
            return lists(from_type(args[0]))
        return lists(integers())  # Default to list[int]

    # Dict[K, V]
    if origin is dict:
        if len(args) >= 2:
            return dicts(from_type(args[0]), from_type(args[1]))
        return dicts(text(), integers())  # Default

    # Tuple[T, ...]
    if origin is tuple:
        if args:
            return tuples(*[from_type(a) for a in args])
        return tuples()

    # Set[T] - generate as list, convert to set
    if origin is set:
        if args:
            return lists(from_type(args[0])).map(set)
        return lists(integers()).map(set)

    # Check for Enum
    if isinstance(type_hint, type) and issubclass(type_hint, Enum):
        return sampled_from(list(type_hint))

    # Check for dataclass
    if is_dataclass(type_hint) and isinstance(type_hint, type):
        return DataclassGenerator(type_hint)

    raise TypeError(f"Cannot generate values for type: {type_hint}")


# =============================================================================
# @hegel decorator with shared hegeld process
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
        self._verbosity = Verbosity.NORMAL
        self.__lock = threading.Lock()

    def __has_working_client(self):
        return self._client is not None and self._connection.live

    def _start(self, verbosity: Verbosity) -> None:
        """Start hegeld if not already running."""
        if self.__has_working_client():
            return

        with self.__lock:
            if self.__has_working_client():
                return
            self._verbosity = verbosity
            self._temp_dir = tempfile.TemporaryDirectory(prefix="hegel-")
            socket_path = os.path.join(self._temp_dir.name, "hegel.sock")

            hegel_cmd = _find_hegeld()
            cmd_args = [
                *hegel_cmd.split(),
                socket_path,
                "--verbosity",
                verbosity.value,
            ]

            if verbosity in (Verbosity.VERBOSE, Verbosity.DEBUG):
                print(f"Starting hegeld: {' '.join(cmd_args)}", file=sys.stderr)

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

            if verbosity in (Verbosity.VERBOSE, Verbosity.DEBUG):
                print("Connected to hegeld", file=sys.stderr)

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
        verbosity: Verbosity,
    ) -> None:
        """Run a property test using the shared hegeld process."""
        self._start(verbosity)

        assert self._client is not None
        test_name = test_fn.__name__ if hasattr(test_fn, "__name__") else "test"
        self._client.run_test(test_name, test_fn, test_cases=test_cases)


# Global session instance
_session = _HegelSession()


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
) -> None:
    """Run a property test using the shared hegeld process.

    If the test fails:
    - Re-raises the original exception if there's exactly one minimal failing case
    - Raises an ExceptionGroup if there are multiple distinct minimal failing cases
    """
    _session.run_test(test_fn, test_cases, verbosity)
