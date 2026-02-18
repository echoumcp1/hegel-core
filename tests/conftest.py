import contextlib
import socket
from threading import Thread

import pytest
from client import Client

from hegel.protocol import Connection
from hegel.server import run_server_on_connection


@pytest.fixture
def socket_pair():
    s1, s2 = socket.socketpair()
    yield s1, s2
    # suppress because a test might already have closed the socket
    with contextlib.suppress(OSError):
        s1.close()
    with contextlib.suppress(OSError):
        s2.close()


def _make_client():
    server_socket, client_socket = socket.socketpair()
    thread = Thread(
        target=run_server_on_connection,
        args=(Connection(server_socket, name="Server"),),
        daemon=True,
    )
    thread.start()
    client_connection = Connection(client_socket, name="Client")
    client = Client(client_connection)
    return client, client_connection, thread


@pytest.fixture
def client():
    client, conn, thread = _make_client()
    yield client
    conn.close()
    thread.join(timeout=5)
