"""Tests for server.py uncovered paths."""

import socket
import time
from threading import Thread
from unittest.mock import patch

import pytest
from client import (
    Client,
    _request,
    assume,
    collection,
    generate_from_schema,
    start_span,
    stop_span,
    target,
)
from hypothesis import strategies as st
from hypothesis.errors import UnsatisfiedAssumption

from hegel.protocol import ProtocolError, RequestError
from hegel.protocol.connection import Connection
from hegel.server import (
    FROM_SCHEMA_CACHE,
    cached_from_schema,
    run_server_on_connection,
)


def test_start_and_stop_span(client):
    def test():
        start_span(1)
        _x = generate_from_schema({"type": "integer", "min_value": 0, "max_value": 10})
        stop_span()

    client.run_test("test_spans", test, test_cases=10)


def test_stop_span_with_discard(client):
    def test():
        start_span(1)
        _x = generate_from_schema({"type": "integer", "min_value": 0, "max_value": 10})
        stop_span(discard=True)

    client.run_test("test_spans_discard", test, test_cases=10)


def test_unknown_command(client):
    with pytest.raises(ProtocolError):
        client._control.send_request({"command": "bogus"}).get()


def test_unknown_command_on_data_channel(client):
    """Unknown command on data channel raises RequestError via handle_requests."""

    def test():
        with pytest.raises(RequestError, match="Unknown command"):
            _request({"command": "bogus_data_command"})

    client.run_test("test_unknown_data_cmd", test, test_cases=1)


def test_cache_eviction():
    # Fill the cache beyond its max size
    for i in range(FROM_SCHEMA_CACHE.max_size + 10):
        schema = {"type": "integer", "min_value": i, "max_value": i + 100}
        cached_from_schema(schema)

    assert len(FROM_SCHEMA_CACHE) <= FROM_SCHEMA_CACHE.max_size


def test_collection_with_no_max_size(client):
    def test():
        c = collection("test_unbounded", min_size=1)
        result = []
        while c.more():
            val = generate_from_schema(
                {"type": "integer", "min_value": 0, "max_value": 100},
            )
            result.append(val)
        assert len(result) >= 1

    client.run_test("test_collection_no_max", test, test_cases=10)


def test_collection_reject_on_server(client):
    """Test collection_reject command is handled by the server.

    Tests that the server handles the collection_reject command by calling
    collection.reject() with the provided reason.
    """

    def test():
        # Explicitly use collection.reject() to trigger the server-side
        # collection_reject handler.
        c = collection("test_coll", min_size=1, max_size=5)
        result = []
        while c.more():
            val = generate_from_schema(
                {"type": "integer", "min_value": 0, "max_value": 100},
            )
            if val % 2 != 0:
                c.reject()
            else:
                result.append(val)

    client.run_test("test_collection_reject", test, test_cases=20)


def test_unsatisfied_assumption_in_handler(client):
    class AlwaysRejectStrategy(st.SearchStrategy):
        def do_draw(self, data):
            raise UnsatisfiedAssumption

    def test():
        generate_from_schema({"type": "integer"})

    with patch("hegel.server.cached_from_schema", return_value=AlwaysRejectStrategy()):
        client.run_test("test_unsatisfied_in_handler", test, test_cases=10)


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
        args=(Connection(server_socket),),
        daemon=True,
    )
    thread.start()

    with Connection(client_socket) as client_connection:
        client = Client(client_connection)

        # Send a run_test request, then immediately close
        channel = client_connection.new_channel(role="Test")
        client._control.send_request(
            {
                "command": "run_test",
                "name": "doomed_test",
                "channel_id": channel.channel_id,
                "test_cases": 100,
                "seed": None,
            },
        ).get()

        # Give the server a moment to start handling the test
        time.sleep(0.1)
        # Close the client connection — this causes the server to get ConnectionError
        # both in the main loop and in _run_one

    thread.join(timeout=10)


def test_base_exception_in_server():
    """Test that BaseException in server loop is caught and printed.

    Tests the except BaseException handler in run_server_on_connection's main
    loop, which catches non-ConnectionError exceptions and prints the traceback.
    We let the handshake complete normally, then patch receive_request to raise
    KeyboardInterrupt on the next call (the main loop).
    """
    server_socket, client_socket = socket.socketpair()
    server_conn = Connection(server_socket)

    original_receive = server_conn.control_channel.read_request
    call_count = 0

    def patched_receive(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count > 1:
            raise KeyboardInterrupt("simulated")
        return original_receive(*args, **kwargs)

    def server():
        with patch.object(
            server_conn.control_channel,
            "read_request",
            side_effect=patched_receive,
        ):
            run_server_on_connection(server_conn)

    thread = Thread(target=server, daemon=True)
    thread.start()

    with Connection(client_socket) as client_conn:
        client_conn.send_handshake()
        time.sleep(0.3)
    thread.join(timeout=5)


def test_passing(client):
    def test():
        x = generate_from_schema({"type": "integer", "min_value": 0, "max_value": 100})
        assert x >= 0
        assert x <= 100

    client.run_test("test_simple", test, test_cases=50)


def test_failing(client):
    def test():
        assert (
            generate_from_schema({"type": "integer", "min_value": 0, "max_value": 1000})
            <= 10
        )

    with pytest.raises(AssertionError):
        client.run_test("test_fail", test, test_cases=100)


def test_assume(client):
    def test():
        x = generate_from_schema({"type": "integer", "min_value": 0, "max_value": 100})
        assume(x % 2 == 0)
        assert x % 2 == 0

    client.run_test("test_assume", test, test_cases=100)


def test_multiple_tests_on_connection(client):
    def test1():
        x = generate_from_schema({"type": "integer"})
        assert isinstance(x, int)

    def test2():
        s = generate_from_schema({"type": "string", "min_size": 0, "max_size": 10})
        assert isinstance(s, str)

    client.run_test("test1", test1, test_cases=20)
    client.run_test("test2", test2, test_cases=20)


def test_target(client):
    def test():
        x = generate_from_schema({"type": "integer", "min_value": 0, "max_value": 100})
        target(float(x), "size")

    client.run_test("test_target", test, test_cases=50)
