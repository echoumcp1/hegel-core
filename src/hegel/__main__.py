"""
Hegel CLI - Property-based testing server.

This module provides the command-line interface for running the Hegel server,
which drives property-based test execution via Hypothesis.
"""

import os
import socket
import sys
import tempfile

import click

from hegel.hegeld import run_server_on_connection
from hegel.protocol import Connection


@click.command()
@click.option(
    "--socket",
    "socket_path",
    default=None,
    help="Unix socket path to listen on. If not provided, uses a temp file and prints the path.",
)
@click.option(
    "--verbosity",
    type=click.Choice(["quiet", "normal", "verbose", "debug"]),
    default="normal",
    help="Verbosity level: quiet, normal, verbose, or debug",
)
def main(socket_path, verbosity):
    """Run the Hegel test server.

    The server listens on a Unix socket and handles test execution requests
    from SDK clients.
    """
    if verbosity == "debug":
        os.environ["HEGEL_DEBUG"] = "true"

    # Create socket
    if socket_path is None:
        # Create a temp socket path
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


if __name__ == "__main__":
    main()
