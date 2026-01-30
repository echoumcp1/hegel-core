"""
Hegel CLI - Property-based testing server.

This module provides the command-line interface for running the Hegel server,
which drives property-based test execution via Hypothesis.

The SDK creates a socket path and spawns hegeld with that path. Hegeld binds to
the socket and serves requests. The SDK then connects to the socket as a client.
"""

import contextlib
import os
import socket
import sys

import click

from hegel.hegeld import run_server_on_connection
from hegel.protocol import Connection


@click.command()
@click.argument("socket_path")
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
def main(socket_path, test_cases, verbosity):
    """Run the Hegel test server, binding to SOCKET_PATH.

    The server binds to the Unix socket and waits for the SDK to connect.
    Once connected, it handles test execution requests over a single persistent
    connection.
    """
    if verbosity == "debug":
        os.environ["HEGEL_DEBUG"] = "true"

    # Store test_cases in environment for hegeld to pick up
    os.environ["HEGEL_TEST_CASES"] = str(test_cases)

    run_server(socket_path, verbosity)


def run_server(socket_path, verbosity):
    """Bind to the socket and serve test execution requests."""
    # Clean up any existing socket
    with contextlib.suppress(FileNotFoundError):
        os.unlink(socket_path)

    # Create and bind server socket
    server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server_sock.bind(socket_path)
    server_sock.listen(1)

    if verbosity in ("verbose", "debug"):
        print(f"Listening on {socket_path}", file=sys.stderr)

    try:
        # Accept a single connection from the SDK
        client_sock, _ = server_sock.accept()

        if verbosity in ("verbose", "debug"):
            print("SDK connected", file=sys.stderr)

        # Handle the connection
        connection = Connection(client_sock, name="Server")
        run_server_on_connection(connection)

        if verbosity in ("verbose", "debug"):
            print("SDK disconnected", file=sys.stderr)

    finally:
        server_sock.close()
        with contextlib.suppress(FileNotFoundError):
            os.unlink(socket_path)


if __name__ == "__main__":  # pragma: no cover
    main()
