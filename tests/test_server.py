"""Tests for server.py uncovered paths."""

import socket
import time
from threading import Thread
from unittest.mock import patch

import pytest
from client import (
    Client,
    _get_channel,
    collection as sdk_collection,
    generate_from_schema,
    start_span,
    stop_span,
    target,
)
from hypothesis import strategies as st

from hegel.protocol import Connection, RequestError
from hegel.server import (
    FROM_SCHEMA_CACHE,
    cached_from_schema,
    run_server_on_connection,
)


def test_start_and_stop_span(client):
    """Test start_span and stop_span commands."""

    def my_test():
        start_span(1)
        x = generate_from_schema({"type": "integer", "minimum": 0, "maximum": 10})
        stop_span()
        assert isinstance(x, int)

    client.run_test("test_spans", my_test, test_cases=10)


def test_stop_span_with_discard(client):
    """Test stop_span with discard=True."""

    def my_test():
        start_span(1)
        x = generate_from_schema({"type": "integer", "minimum": 0, "maximum": 10})
        stop_span(discard=True)
        assert isinstance(x, int)

    client.run_test("test_spans_discard", my_test, test_cases=10)


def test_target_observations_on_server(client):
    """Test target command is handled by server."""

    def my_test():
        x = generate_from_schema({"type": "integer", "minimum": 0, "maximum": 100})
        target(float(x), "maximize_x")
        assert x >= 0

    client.run_test("test_target", my_test, test_cases=10)


def test_mark_interesting(client):
    """Test that failing test cases are marked as INTERESTING."""

    def my_test():
        x = generate_from_schema({"type": "integer", "minimum": 0, "maximum": 100})
        assert x < 50

    with pytest.raises(AssertionError):
        client.run_test("test_interesting", my_test, test_cases=100)


def test_unknown_command_on_server(client):
    """Test server responds with error to unknown commands on control channel."""
    with pytest.raises(RequestError, match="Unknown command"):
        client._control.request({"command": "bogus"}).get()


def test_cache_eviction():
    # Fill the cache beyond its max size
    for i in range(FROM_SCHEMA_CACHE.max_size + 10):
        schema = {"type": "integer", "minimum": i, "maximum": i + 100}
        cached_from_schema(schema)

    assert len(FROM_SCHEMA_CACHE) <= FROM_SCHEMA_CACHE.max_size


def test_unknown_command_in_test_case(client):
    """Test that an unknown command in test case handler raises ValueError."""

    def my_test():
        channel = _get_channel()
        # Send an unknown command on the test case channel
        with pytest.raises(RequestError):
            channel.request({"command": "bogus_command"}).get()

    # The test should still complete even though the command fails
    client.run_test("test_unknown_cmd", my_test, test_cases=1)


def test_mark_interesting_status(client):
    """Test that INTERESTING status is handled by the server."""
    call_count = [0]

    def my_test():
        call_count[0] += 1
        generate_from_schema(
            {
                "type": "integer",
                "minimum": 0,
                "maximum": 1000,
            },
        )
        # Always fail - this will mark test cases as INTERESTING
        raise AssertionError

    with pytest.raises(AssertionError):
        client.run_test("test_mark_interesting", my_test, test_cases=20)


def test_unsatisfied_assumption_handled_gracefully(client):
    """Test that UnsatisfiedAssumption from data.draw() is handled as invalid.

    When a Hypothesis strategy raises UnsatisfiedAssumption (e.g., st.nothing()),
    the server should call data.mark_invalid() instead of crashing. This converts
    it to a StopTest which the SDK handles as DataExhausted.
    """

    def my_test():
        # st.nothing() always raises UnsatisfiedAssumption when drawn.
        # The server should handle this gracefully by marking the test
        # case as invalid rather than crashing.
        generate_from_schema({"type": "integer"})

    # Mock cached_from_schema to return st.nothing(), which always
    # raises UnsatisfiedAssumption on draw.
    with patch("hegel.server.cached_from_schema", return_value=st.nothing()):
        # All test cases will be marked invalid (UnsatisfiedAssumption),
        # so no interesting examples are found and the test "passes".
        client.run_test("test_unsatisfied", my_test, test_cases=10)


def test_collection_with_no_max_size(client):
    """Test collection with max_size=None (unbounded)."""

    def my_test():
        c = sdk_collection("test_unbounded", min_size=1)
        result = []
        while c.more():
            val = generate_from_schema(
                {"type": "integer", "minimum": 0, "maximum": 100},
            )
            result.append(val)
        assert len(result) >= 1

    client.run_test("test_collection_no_max", my_test, test_cases=10)


def test_collection_reject_on_server(client):
    """Test collection_reject command is handled by the server.

    Tests that the server handles the collection_reject command by calling
    collection.reject() with the provided reason.
    """

    def my_test():
        # Explicitly use collection.reject() to trigger the server-side
        # collection_reject handler.
        c = sdk_collection("test_coll", min_size=1, max_size=5)
        result = []
        while c.more():
            val = generate_from_schema(
                {"type": "integer", "minimum": 0, "maximum": 100},
            )
            if val % 2 != 0:
                c.reject()
            else:
                result.append(val)

    client.run_test("test_collection_reject", my_test, test_cases=20)


def test_mark_complete_unknown_status(client):
    """Test mark_complete with an unknown status (not VALID/INVALID/INTERESTING).

    Tests the fallthrough branch in mark_complete where status is not
    VALID, INVALID, or INTERESTING (no conclude/mark method is called).
    """

    def my_test():
        generate_from_schema({"type": "integer", "minimum": 0, "maximum": 10})
        # Send mark_complete with an unknown status
        channel = _get_channel()
        channel.request(
            {"command": "mark_complete", "status": "UNKNOWN_STATUS"},
        ).get()

    client.run_test("test_unknown_status", my_test, test_cases=1)


def test_unsatisfied_assumption_in_handler(client):
    """Test UnsatisfiedAssumption from strategy draw is handled as invalid.

    Tests the except UnsatisfiedAssumption handler in handle_sdk_request
    which catches the exception and marks the test case as invalid.
    Uses a custom strategy that raises UnsatisfiedAssumption directly in do_draw().
    """
    from hypothesis.errors import UnsatisfiedAssumption

    class AlwaysRejectStrategy(st.SearchStrategy):
        def do_draw(self, data):
            raise UnsatisfiedAssumption

    def my_test():
        generate_from_schema({"type": "integer"})

    with patch("hegel.server.cached_from_schema", return_value=AlwaysRejectStrategy()):
        client.run_test("test_unsatisfied_in_handler", my_test, test_cases=10)


def test_future_cancel_on_connection_error():
    """Test that pending futures with ConnectionError are cancelled.

    Tests the except (ConnectionError, TimeoutError): f.cancel() branch
    in run_server_on_connection's cleanup. When the client disconnects while
    a test is running, _run_one raises ConnectionError. After the
    executor shuts down, f.result() re-raises that ConnectionError, which
    is caught, and f.cancel() is called.
    """
    server_socket, client_socket = socket.socketpair()
    thread = Thread(
        target=run_server_on_connection,
        args=(Connection(server_socket, name="Server"),),
        daemon=True,
    )
    thread.start()

    client_connection = Connection(client_socket, name="Client")
    client = Client(client_connection)

    # Send a run_test request, then immediately close
    channel = client_connection.new_channel(role="Test")
    client._control.request(
        {
            "command": "run_test",
            "name": "doomed_test",
            "channel": channel.channel_id,
            "test_cases": 100,
        },
    ).get()

    # Give the server a moment to start handling the test
    time.sleep(0.1)
    # Close the client connection — this causes the server to get ConnectionError
    # both in the main loop and in _run_one
    client_connection.close()

    thread.join(timeout=10)


def test_base_exception_in_server():
    """Test that BaseException in server loop is caught and printed.

    Tests the except BaseException handler in run_server_on_connection's main
    loop, which catches non-ConnectionError exceptions and prints the traceback.
    We patch receive_request (used in the while loop) to raise KeyboardInterrupt.
    receive_handshake uses receive_request_raw so it is unaffected by the patch.
    """
    server_socket, client_socket = socket.socketpair()
    server_conn = Connection(server_socket, name="Server")

    def server():
        with patch.object(
            server_conn.control_channel,
            "receive_request",
            side_effect=KeyboardInterrupt("simulated"),
        ):
            run_server_on_connection(server_conn)

    thread = Thread(target=server, daemon=True)
    thread.start()

    client_conn = Connection(client_socket, name="Client")
    client_conn.send_handshake()

    time.sleep(0.3)
    client_conn.close()
    thread.join(timeout=5)
