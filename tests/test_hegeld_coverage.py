"""Tests for hegeld.py uncovered paths."""

import socket
import time
from threading import Thread
from unittest.mock import patch

import pytest

from hegel.hegeld import (
    CACHE_SIZE,
    FROM_SCHEMA_CACHE,
    cached_from_schema,
    run_server_on_connection,
)
from hegel.protocol import Connection, RequestError
from hegel.sdk import (
    Client,
    _get_channel,
    generate_from_schema as draw,
    start_span,
    stop_span,
    target,
)


def test_start_and_stop_span():
    """Test start_span and stop_span commands."""
    server_socket, client_socket = socket.socketpair()
    thread = Thread(
        target=run_server_on_connection,
        args=(Connection(server_socket, name="Server"),),
        daemon=True,
    )
    thread.start()

    try:
        client_connection = Connection(client_socket, name="Client")
        client = Client(client_connection)

        def my_test():
            start_span(1)
            x = draw({"type": "integer", "minimum": 0, "maximum": 10})
            stop_span()
            assert isinstance(x, int)

        client.run_test("test_spans", my_test, test_cases=10)
    finally:
        client_connection.close()

    thread.join(timeout=5)


def test_stop_span_with_discard():
    """Test stop_span with discard=True."""
    server_socket, client_socket = socket.socketpair()
    thread = Thread(
        target=run_server_on_connection,
        args=(Connection(server_socket, name="Server"),),
        daemon=True,
    )
    thread.start()

    try:
        client_connection = Connection(client_socket, name="Client")
        client = Client(client_connection)

        def my_test():
            start_span(1)
            x = draw({"type": "integer", "minimum": 0, "maximum": 10})
            stop_span(discard=True)
            assert isinstance(x, int)

        client.run_test("test_spans_discard", my_test, test_cases=10)
    finally:
        client_connection.close()

    thread.join(timeout=5)


def test_target_observations_on_server():
    """Test target command is handled by server."""
    server_socket, client_socket = socket.socketpair()
    thread = Thread(
        target=run_server_on_connection,
        args=(Connection(server_socket, name="Server"),),
        daemon=True,
    )
    thread.start()

    try:
        client_connection = Connection(client_socket, name="Client")
        client = Client(client_connection)

        def my_test():
            x = draw({"type": "integer", "minimum": 0, "maximum": 100})
            target(float(x), "maximize_x")
            assert x >= 0

        client.run_test("test_target", my_test, test_cases=10)
    finally:
        client_connection.close()

    thread.join(timeout=5)


def test_mark_interesting():
    """Test that failing test cases are marked as INTERESTING."""
    server_socket, client_socket = socket.socketpair()
    thread = Thread(
        target=run_server_on_connection,
        args=(Connection(server_socket, name="Server"),),
        daemon=True,
    )
    thread.start()

    try:
        client_connection = Connection(client_socket, name="Client")
        client = Client(client_connection)

        def my_test():
            x = draw({"type": "integer", "minimum": 0, "maximum": 100})
            assert x < 50

        with pytest.raises(AssertionError):
            client.run_test("test_interesting", my_test, test_cases=100)
    finally:
        client_connection.close()

    thread.join(timeout=5)


def test_unknown_command_on_server():
    """Test server responds with error to unknown commands on control channel."""
    server_socket, client_socket = socket.socketpair()
    thread = Thread(
        target=run_server_on_connection,
        args=(Connection(server_socket, name="Server"),),
        daemon=True,
    )
    thread.start()

    try:
        client_connection = Connection(client_socket, name="Client")
        client = Client(client_connection)

        # Send an unknown command on the control channel
        with pytest.raises(RequestError, match="Unknown command"):
            client._control.request({"command": "bogus"}).get()
    finally:
        client_connection.close()

    thread.join(timeout=5)


def test_cache_eviction():
    """Test schema cache eviction when exceeding CACHE_SIZE."""
    # Fill the cache beyond CACHE_SIZE
    for i in range(CACHE_SIZE + 10):
        schema = {"type": "integer", "minimum": i, "maximum": i + 100}
        cached_from_schema(schema)

    assert len(FROM_SCHEMA_CACHE) <= CACHE_SIZE

    # Clear out what we added
    FROM_SCHEMA_CACHE.clear()


def test_unknown_command_in_test_case():
    """Test that an unknown command in test case handler raises ValueError."""
    server_socket, client_socket = socket.socketpair()
    thread = Thread(
        target=run_server_on_connection,
        args=(Connection(server_socket, name="Server"),),
        daemon=True,
    )
    thread.start()

    try:
        client_connection = Connection(client_socket, name="Client")
        client = Client(client_connection)

        def my_test():
            channel = _get_channel()
            # Send an unknown command on the test case channel
            with pytest.raises(RequestError):
                channel.request({"command": "bogus_command"}).get()

        # The test should still complete even though the command fails
        client.run_test("test_unknown_cmd", my_test, test_cases=1)
    finally:
        client_connection.close()

    thread.join(timeout=5)


def test_mark_interesting_status():
    """Test that INTERESTING status is handled by the server."""
    server_socket, client_socket = socket.socketpair()
    thread = Thread(
        target=run_server_on_connection,
        args=(Connection(server_socket, name="Server"),),
        daemon=True,
    )
    thread.start()

    try:
        client_connection = Connection(client_socket, name="Client")
        client = Client(client_connection)

        call_count = [0]

        def my_test():
            call_count[0] += 1
            draw(
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
    finally:
        client_connection.close()

    thread.join(timeout=5)


def test_base_exception_in_server():
    """Test that BaseException in server loop is caught and printed.

    This covers hegeld.py lines 180-181 (except BaseException: traceback.print_exc()).
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
