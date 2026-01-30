"""Tests for __main__.py CLI and run_server."""

import os
import socket
import tempfile
from threading import Thread

from click.testing import CliRunner

from hegel.__main__ import main, run_server
from hegel.protocol import Connection
from hegel.sdk import Client


def _wait_and_connect(socket_path, timeout=5.0):
    """Wait for a Unix socket to appear and connect to it."""
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(socket_path):
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.connect(socket_path)
                return sock
            except (ConnectionRefusedError, FileNotFoundError, OSError):
                sock.close()
        time.sleep(0.05)
    raise RuntimeError(f"Timeout waiting for socket at {socket_path}")


def test_run_server_accepts_connection():
    """Test run_server accepts a connection and handles it."""
    with tempfile.TemporaryDirectory() as d:
        socket_path = os.path.join(d, "test.sock")

        def server():
            run_server(socket_path, "quiet")

        t = Thread(target=server, daemon=True)
        t.start()

        sock = _wait_and_connect(socket_path)
        conn = Connection(sock, name="Client")
        client = Client(conn)

        def my_test():
            pass

        client.run_test("test", my_test, test_cases=1)
        conn.close()
        t.join(timeout=5)


def test_run_server_verbose():
    """Test run_server with verbose output."""
    with tempfile.TemporaryDirectory() as d:
        socket_path = os.path.join(d, "test.sock")

        def server():
            run_server(socket_path, "verbose")

        t = Thread(target=server, daemon=True)
        t.start()

        sock = _wait_and_connect(socket_path)
        conn = Connection(sock, name="Client")
        client = Client(conn)

        def my_test():
            pass

        client.run_test("test", my_test, test_cases=1)
        conn.close()
        t.join(timeout=5)


def test_run_server_cleans_up_stale_socket():
    """Test run_server removes an existing socket file."""
    with tempfile.TemporaryDirectory() as d:
        socket_path = os.path.join(d, "test.sock")
        # Create stale socket file
        with open(socket_path, "w"):
            pass

        def server():
            run_server(socket_path, "quiet")

        t = Thread(target=server, daemon=True)
        t.start()

        sock = _wait_and_connect(socket_path)
        conn = Connection(sock, name="Client")
        client = Client(conn)

        def my_test():
            pass

        client.run_test("test", my_test, test_cases=1)
        conn.close()
        t.join(timeout=5)


def test_main_cli_debug_mode():
    """Test the main CLI sets debug env var."""
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as d:
        socket_path = os.path.join(d, "test.sock")

        def run_cli():
            runner.invoke(
                main,
                [socket_path, "--verbosity", "debug", "--test-cases", "5"],
                catch_exceptions=False,
            )

        t = Thread(target=run_cli, daemon=True)
        t.start()

        sock = _wait_and_connect(socket_path)
        conn = Connection(sock, name="Client")
        client = Client(conn)

        def my_test():
            pass

        client.run_test("test", my_test, test_cases=1)
        conn.close()
        t.join(timeout=5)


def test_main_cli_normal_mode():
    """Test the main CLI with normal verbosity."""
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as d:
        socket_path = os.path.join(d, "test.sock")

        def run_cli():
            runner.invoke(
                main,
                [socket_path],
                catch_exceptions=False,
            )

        t = Thread(target=run_cli, daemon=True)
        t.start()

        sock = _wait_and_connect(socket_path)
        conn = Connection(sock, name="Client")
        client = Client(conn)

        def my_test():
            pass

        client.run_test("test", my_test, test_cases=1)
        conn.close()
        t.join(timeout=5)
