"""
Hegel Python SDK - Reference implementation for writing property tests.

This SDK connects to a Hegel server and provides the API for writing
property-based tests.
"""

import sys
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

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
            test_fn: The test function to run. Should use draw(), assume(), etc.
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


def draw(schema: dict) -> Any:
    """Generate a value from a schema.

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


def assume(condition: bool) -> None:
    """Reject the current test case if condition is False.

    Use this to filter out invalid inputs. Hypothesis will generate
    new inputs rather than counting this as a failure.

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

    Hypothesis will try to find inputs that maximize the target value.
    This can help find edge cases more quickly.

    Args:
        value: A numeric value to maximize.
        label: Optional label for this target (useful if targeting multiple values).
    """
    channel = _get_channel()
    channel.request({"command": "target", "value": value, "label": label}).get()


def start_span(label: int = 0) -> None:
    """Start a generation span for better shrinking.

    Spans help Hypothesis understand the structure of generated data,
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


# Convenience strategy builders


@dataclass
class Strategy:
    """A strategy for generating values."""

    schema: dict

    def draw(self) -> Any:
        """Generate a value from this strategy."""
        return draw(self.schema)


def integers(min_value: int | None = None, max_value: int | None = None) -> Strategy:
    """Strategy for generating integers.

    Args:
        min_value: Minimum value (inclusive), or None for unbounded.
        max_value: Maximum value (inclusive), or None for unbounded.
    """
    schema: dict = {"type": "integer"}
    if min_value is not None:
        schema["minimum"] = min_value
    if max_value is not None:
        schema["maximum"] = max_value
    return Strategy(schema)


def floats(
    min_value: float | None = None,
    max_value: float | None = None,
    *,
    allow_nan: bool = False,
    allow_infinity: bool = False,
) -> Strategy:
    """Strategy for generating floats.

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
    return Strategy(schema)


def booleans(p: float = 0.5) -> Strategy:
    """Strategy for generating booleans.

    Args:
        p: Probability of True.
    """
    return Strategy({"type": "boolean", "p": p})


def text(min_size: int = 0, max_size: int | None = None) -> Strategy:
    """Strategy for generating text strings.

    Args:
        min_size: Minimum length.
        max_size: Maximum length, or None for unbounded.
    """
    schema: dict = {"type": "string", "min_size": min_size}
    if max_size is not None:
        schema["max_size"] = max_size
    return Strategy(schema)


def binary(min_size: int = 0, max_size: int | None = None) -> Strategy:
    """Strategy for generating binary data (as base64).

    Args:
        min_size: Minimum length in bytes.
        max_size: Maximum length in bytes, or None for unbounded.
    """
    schema: dict = {"type": "binary", "min_size": min_size}
    if max_size is not None:
        schema["max_size"] = max_size
    return Strategy(schema)


def lists(
    elements: Strategy | dict,
    min_size: int = 0,
    max_size: int | None = None,
) -> Strategy:
    """Strategy for generating lists.

    Args:
        elements: Strategy or schema for list elements.
        min_size: Minimum length.
        max_size: Maximum length, or None for unbounded.
    """
    elem_schema = elements.schema if isinstance(elements, Strategy) else elements
    schema: dict = {"type": "list", "elements": elem_schema, "min_size": min_size}
    if max_size is not None:
        schema["max_size"] = max_size
    return Strategy(schema)


def tuples(*elements: Strategy | dict) -> Strategy:
    """Strategy for generating tuples.

    Args:
        *elements: Strategies or schemas for each tuple element.
    """
    elem_schemas = [e.schema if isinstance(e, Strategy) else e for e in elements]
    return Strategy({"type": "tuple", "elements": elem_schemas})


def just(value: Any) -> Strategy:
    """Strategy that always returns the same value.

    Args:
        value: The constant value to return.
    """
    return Strategy({"const": value})


def sampled_from(values: list) -> Strategy:
    """Strategy that samples from a list of values.

    Args:
        values: The values to sample from.
    """
    return Strategy({"sampled_from": values})


def one_of(*strategies: Strategy | dict) -> Strategy:
    """Strategy that picks from one of several strategies.

    Args:
        *strategies: Strategies or schemas to choose from.
    """
    schemas = [s.schema if isinstance(s, Strategy) else s for s in strategies]
    return Strategy({"one_of": schemas})
