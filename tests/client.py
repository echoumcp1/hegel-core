import sys
import threading
from collections.abc import Callable
from contextvars import ContextVar
from typing import Any

try:
    ExceptionGroup
except NameError:  # pragma: no cover
    from exceptiongroup import ExceptionGroup

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


class Client:
    """Test client for connecting to a Hegel server."""

    def __init__(self, connection: Connection):
        connection.send_handshake()

        self.connection = connection
        self._control = connection.control_channel
        self.__lock = threading.Lock()

    def run_test(
        self,
        name: str,
        test_fn: Callable[[], None],
        *,
        test_cases: int,
        seed: int,
    ) -> None:
        """Run a property test."""

        test_channel = self.connection.new_channel(role="Test")

        with self.__lock:
            self._control.request(
                {
                    "command": "run_test",
                    "name": name,
                    "test_cases": test_cases,
                    "seed": seed,
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
        if _current_channel.get() is not None:
            raise RuntimeError(
                "Cannot nest test cases - already inside a test case",
            )
        _current_channel.set(channel)
        _is_final.set(is_final)
        _test_aborted.set(False)
        already_complete = False
        status = "VALID"
        origin = None
        try:
            test_fn()
        except AssumeRejected:
            status = "INVALID"
        except DataExhausted:
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
            _current_channel.set(None)
            _is_final.set(False)
            _test_aborted.set(False)
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


def generate_from_schema(schema: dict) -> Any:
    """Generate a value from a schema."""
    channel = _get_channel()
    try:
        return channel.request({"command": "generate", "schema": schema}).get()
    except RequestError as e:
        if e.error_type == "StopTest":
            _test_aborted.set(True)
            raise DataExhausted("Server ran out of data") from e
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
    if _test_aborted.get():
        return
    channel = _get_channel()
    channel.request({"command": "start_span", "label": label}).get()


def stop_span(*, discard: bool = False) -> None:
    """End the current generation span."""
    if _test_aborted.get():
        return
    channel = _get_channel()
    channel.request({"command": "stop_span", "discard": discard}).get()


class collection:
    def __init__(
        self, name: str | None, min_size: int = 0, max_size: int | None = None
    ):
        self.__base_name = name
        self.__server_name = None
        self.__finished = False
        self.min_size = min_size
        self.max_size = max_size

    @property
    def _server_name(self):
        if self.__server_name is None:
            self.__server_name = (
                _get_channel().request(
                    {
                        "command": "new_collection",
                        "name": self.__base_name,
                        "min_size": self.min_size,
                        "max_size": self.max_size,
                    }
                )
            ).get()
        return self.__server_name

    def more(self) -> bool:
        """Should we generate another element?"""
        if self.__finished:
            return False

        result = (
            _get_channel()
            .request({"command": "collection_more", "collection": self._server_name})
            .get()
        )
        if not result:
            self.__finished = True
        return result

    def reject(self, why: str | None = None) -> None:
        """We did not add the last element to the collection,
        don't count it towards our size budget."""
        if not self.__finished:
            return (
                _get_channel()
                .request(
                    {
                        "command": "collection_reject",
                        "collection": self._server_name,
                        "why": why,
                    }
                )
                .get()
            )
