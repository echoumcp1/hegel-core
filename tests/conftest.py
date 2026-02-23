import contextlib
import socket as socket_module
from threading import Thread

import pytest
from client import Client

from hegel.protocol.connection import Connection
from hegel.server import run_server_on_connection


@pytest.fixture
def socket_pair():
    s1, s2 = socket_module.socketpair()
    yield s1, s2
    # suppress because a test might already have closed the socket
    with contextlib.suppress(OSError):
        s1.close()
    with contextlib.suppress(OSError):
        s2.close()


@pytest.fixture
def socket():
    s = socket_module.socket()
    yield s
    with contextlib.suppress(OSError):
        s.close()


def _make_client():
    server_socket, client_socket = socket_module.socketpair()
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
    with conn:
        yield client
    thread.join(timeout=5)
