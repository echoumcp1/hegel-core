"""
Hegel CLI - Property-based testing server.

This module provides the command-line interface for running the Hegel server,
which drives property-based test execution via Hypothesis.

Two modes of operation:

1. Server mode (default): Creates a Unix socket and waits for a client to connect.
   The client (SDK) connects and requests test execution.

2. Client mode (--client-mode): Connects to an existing socket created by the SDK.
   Used by the @hegel decorator for embedded test execution.
"""

import os
import socket
import sys

import click

from hegel.hegeld import run_server_on_connection
from hegel.protocol import Connection


@click.command()
@click.option(
    "--socket",
    "socket_path",
    default=None,
    help="Unix socket path. In server mode, creates a socket here. In client mode, connects to this socket.",
)
@click.option(
    "--client-mode",
    "client_mode_socket",
    default=None,
    help="Run in client mode, connecting to the specified socket path. Used by @hegel decorator.",
)
@click.option(
    "--test-cases",
    type=int,
    default=100,
    help="Maximum number of test cases to run (default: 100)",
)
@click.option(
    "--verbosity",
    type=click.Choice(["quiet", "normal", "verbose", "debug"]),
    default="normal",
    help="Verbosity level: quiet, normal, verbose, or debug",
)
def main(socket_path, client_mode_socket, test_cases, verbosity):
    """Run the Hegel test server.

    By default, the server listens on a Unix socket and handles test execution
    requests from SDK clients.

    In client mode (--client-mode), the server connects to an existing socket
    created by the SDK (used by the @hegel decorator).
    """
    if verbosity == "debug":
        os.environ["HEGEL_DEBUG"] = "true"

    # Store test_cases in environment for hegeld to pick up
    os.environ["HEGEL_TEST_CASES"] = str(test_cases)

    if client_mode_socket:
        # Client mode: connect to existing socket
        run_client_mode(client_mode_socket, verbosity)
    else:
        # Server mode: create socket and listen
        run_server_mode(socket_path, verbosity)


def run_server_mode(socket_path, verbosity):
    """Run in server mode - create socket and wait for client."""
    import tempfile

    # Create socket path if not provided
    if socket_path is None:
        fd, socket_path = tempfile.mkstemp(prefix="hegel-", suffix=".sock")
        os.close(fd)
        os.unlink(socket_path)
        print(f"HEGEL_SOCKET={socket_path}", file=sys.stderr)

    # Clean up any existing socket
    try:
        os.unlink(socket_path)
    except FileNotFoundError:
        pass

    # Create and bind server socket
    server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server_sock.bind(socket_path)
    server_sock.listen(1)

    if verbosity in ("verbose", "debug"):
        print(f"Listening on {socket_path}", file=sys.stderr)

    try:
        # Accept a single connection
        client_sock, _ = server_sock.accept()

        if verbosity in ("verbose", "debug"):
            print("Client connected", file=sys.stderr)

        # Handle the connection
        connection = Connection(client_sock, name="Server")
        run_server_on_connection(connection)

        if verbosity in ("verbose", "debug"):
            print("Client disconnected", file=sys.stderr)

    finally:
        server_sock.close()
        try:
            os.unlink(socket_path)
        except FileNotFoundError:
            pass


def run_client_mode(socket_path, verbosity):
    """Run in client mode - connect to existing socket."""
    if verbosity in ("verbose", "debug"):
        print(f"Connecting to {socket_path}", file=sys.stderr)

    # Connect to the SDK's socket
    client_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)

    try:
        client_sock.connect(socket_path)
    except (ConnectionRefusedError, FileNotFoundError) as e:
        print(f"Failed to connect to socket {socket_path}: {e}", file=sys.stderr)
        sys.exit(1)

    if verbosity in ("verbose", "debug"):
        print("Connected to SDK", file=sys.stderr)

    # Handle the connection - hegeld acts as server in protocol terms
    # even though it's the one connecting
    connection = Connection(client_sock, name="Server")
    run_server_on_connection(connection)

    if verbosity in ("verbose", "debug"):
        print("Test execution complete", file=sys.stderr)


if __name__ == "__main__":
    main()
