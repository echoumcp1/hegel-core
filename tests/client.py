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

from hegel.protocol import RequestError
from hegel.protocol.channel import Channel
from hegel.protocol.connection import Connection

SUPPORTED_PROTOCOL_VERSIONS = (0.1, 0.1)

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
        server_version = float(connection.send_handshake())
        lo, hi = SUPPORTED_PROTOCOL_VERSIONS
        if not (lo <= server_version <= hi):
            raise ConnectionError(
                f"hegel-python supports protocol versions {lo} through {hi}, but "
                f"got server version {server_version}. Upgrading hegel-python or downgrading "
                "your hegel cli might help."
            )

        self.connection = connection
        self._control = connection.control_channel
        self.__lock = threading.Lock()

    def run_test(
        self,
        name: str,
        test_fn: Callable[[], None],
        *,
        test_cases: int = 100,
        seed: int | None = None,
    ) -> None:
        """Run a property test."""

        test_channel = self.connection.new_channel(role="Test")

        with self.__lock:
            self._control.send_request(
                {
                    "command": "run_test",
                    "name": name,
                    "test_cases": test_cases,
                    "seed": seed,
                    "channel_id": test_channel.channel_id,
                },
            ).get()

        result_data = None

        test_case_count = 0
        while True:
            packet = test_channel.read_request()
            message = cbor2.loads(packet.payload)
            test_case_count += 1
            event = message.get("event")

            if event == "test_case":
                channel_id = message["channel_id"]
                test_channel.write_reply(packet.message_id, None)
                test_case_channel = self.connection.connect_channel(
                    channel_id,
                    role="Test Case",
                )
                self._run_test_case(test_case_channel, test_fn, is_final=False)
            elif event == "test_done":
                test_channel.write_reply(packet.message_id, True)
                result_data = message["results"]
                break
            else:
                test_channel.write_reply_error(
                    packet.message_id,
                    error=f"Unrecognised event {event}",
                    error_type="InvalidMessage",
                )

        assert result_data is not None

        n_interesting = result_data["interesting_test_cases"]

        if n_interesting == 0:
            return

        exceptions: list[Exception] = []
        for i in range(n_interesting):
            try:
                packet = test_channel.read_request()
                message = cbor2.loads(packet.payload)
                test_case_count += 1
                assert message["event"] == "test_case"

                channel_id = message["channel_id"]
                test_channel.write_reply(packet.message_id, None)
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
                channel.write_request(
                    cbor2.dumps(
                        {
                            "command": "mark_complete",
                            "status": status,
                            "origin": origin,
                        }
                    )
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


def _request(payload: dict) -> Any:
    """Send a request on the current test channel, handling server-side errors.

    Converts server-side StopTest/UnsatisfiedAssumption errors into
    DataExhausted so the client knows the test case is already complete.
    """
    try:
        return _get_channel().send_request(payload).get()
    except RequestError as e:
        if e.error_type == "StopTest":
            _test_aborted.set(True)
            raise DataExhausted("Server ran out of data") from e
        raise


def generate_from_schema(schema: dict) -> Any:
    """Generate a value from a schema."""
    return _request({"command": "generate", "schema": schema})


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
    _request({"command": "target", "value": value, "label": label})


def start_span(label: int = 0) -> None:
    """Start a generation span for better shrinking."""
    if _test_aborted.get():
        return
    _request({"command": "start_span", "label": label})


def stop_span(*, discard: bool = False) -> None:
    """End the current generation span."""
    if _test_aborted.get():
        return
    _request({"command": "stop_span", "discard": discard})


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
            self.__server_name = _request(
                {
                    "command": "new_collection",
                    "name": self.__base_name,
                    "min_size": self.min_size,
                    "max_size": self.max_size,
                }
            )
        return self.__server_name

    def more(self) -> bool:
        """Should we generate another element?"""
        if self.__finished:
            return False

        result = _request(
            {"command": "collection_more", "collection": self._server_name}
        )
        if not result:
            self.__finished = True
        return result

    def reject(self, why: str | None = None) -> None:
        """We did not add the last element to the collection,
        don't count it towards our size budget."""
        if not self.__finished:
            _request(
                {
                    "command": "collection_reject",
                    "collection": self._server_name,
                    "why": why,
                }
            )
