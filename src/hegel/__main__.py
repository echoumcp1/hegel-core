import contextlib
import os
import socket
import sys

import click
from hypothesis import Verbosity

from hegel.protocol import Connection
from hegel.server import run_server_on_connection


@click.command()
@click.argument("socket_path")
@click.option(
    "--test-cases",
    type=int,
    default=100,
    show_default=True,
    help="Maximum number of test cases to run",
)
@click.option(
    "--verbosity",
    type=click.Choice(["quiet", "normal", "verbose", "debug"]),
    default="normal",
    help="Verbosity level. Corresponds to hypothesis.Verbosity.",
)
def main(socket_path, test_cases, verbosity):
    """Run the Hegel test server, binding to socket_path."""
    verbosity = Verbosity(verbosity)

    if verbosity >= Verbosity.debug:
        os.environ["HEGEL_DEBUG"] = "1"
    os.environ["HEGEL_TEST_CASES"] = str(test_cases)

    # Clean up any existing socket before starting
    with contextlib.suppress(FileNotFoundError):
        os.unlink(socket_path)

    run_server(socket_path, verbosity=verbosity)


def run_server(socket_path: str, *, verbosity: Verbosity) -> None:
    server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server_sock.bind(socket_path)
    server_sock.listen(1)

    if verbosity >= Verbosity.verbose:
        print(f"Listening on {socket_path}", file=sys.stderr)

    try:
        client_sock, _ = server_sock.accept()

        if verbosity >= Verbosity.verbose:
            print("SDK connected", file=sys.stderr)

        connection = Connection(client_sock, name="Server")
        run_server_on_connection(connection)

        if verbosity >= Verbosity.verbose:
            print("SDK disconnected", file=sys.stderr)

    finally:
        server_sock.close()


if __name__ == "__main__":  # pragma: no cover
    main()
