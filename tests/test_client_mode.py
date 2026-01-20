import json
import os
import socket
import subprocess
import tempfile
import threading
from typing import Any


def create_server_socket(socket_path: str) -> socket.socket:
    """Create a Unix domain socket server."""
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(socket_path)
    server.listen(1)
    return server


def run_simple_test_server(socket_path: str, responses: list[tuple[str, Any]]):
    """Run a simple test server that handles one connection.

    Args:
        socket_path: Path to create the Unix socket
        responses: List of (result_type, optional_message) tuples.
                   Each tuple corresponds to one test case connection.
    """
    server = create_server_socket(socket_path)
    server.settimeout(10.0)  # 10 second timeout

    for result_type, message in responses:
        try:
            conn, _ = server.accept()
            conn.settimeout(5.0)
            reader = conn.makefile("rb")

            # Read handshake
            handshake_line = reader.readline()
            handshake = json.loads(handshake_line.decode())
            assert handshake.get("type") == "handshake"

            # Send handshake_ack
            ack = {"type": "handshake_ack"}
            conn.sendall((json.dumps(ack) + "\n").encode())

            # Simulate a simple test that makes one generate request
            request = {"id": 1, "command": "generate", "payload": {"type": "integer"}}
            conn.sendall((json.dumps(request) + "\n").encode())

            # Read response
            response_line = reader.readline()
            response = json.loads(response_line.decode())
            assert response.get("id") == 1

            # Send test result
            result_msg = {"type": "test_result", "result": result_type}
            if message:
                result_msg["message"] = message
            conn.sendall((json.dumps(result_msg) + "\n").encode())

            conn.close()
        except TimeoutError:
            break

    server.close()


def test_client_mode_connects_and_handshakes():
    """Test that client mode can connect and complete handshake."""
    with tempfile.TemporaryDirectory() as tmpdir:
        socket_path = os.path.join(tmpdir, "test.sock")

        # Start server in background thread
        server_thread = threading.Thread(
            target=run_simple_test_server,
            args=(socket_path, [("pass", None)]),
        )
        server_thread.start()

        # Give server time to start
        import time

        time.sleep(0.1)

        # Run hegel in client mode
        result = subprocess.run(
            ["hegel", "--client-mode", socket_path, "--test-cases", "1", "--no-tui"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        server_thread.join(timeout=5)

        # Should exit successfully for passing test
        assert result.returncode == 0


def test_client_mode_handles_test_failure():
    """Test that client mode handles test failures correctly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        socket_path = os.path.join(tmpdir, "test.sock")

        def server_logic():
            """Server that always reports failure for each connection."""
            server = create_server_socket(socket_path)
            server.settimeout(15.0)

            # Accept up to 50 connections to handle hypothesis shrinking
            # (hypothesis does extensive shrinking which may require many connections)
            for _ in range(50):
                try:
                    conn, _ = server.accept()
                    conn.settimeout(5.0)
                    reader = conn.makefile("rb")

                    # Read handshake
                    handshake_line = reader.readline()
                    if not handshake_line:
                        conn.close()
                        continue
                    handshake = json.loads(handshake_line.decode())
                    assert handshake.get("type") == "handshake"
                    is_last_run = handshake.get("is_last_run", False)

                    # Send handshake_ack
                    ack = {"type": "handshake_ack"}
                    conn.sendall((json.dumps(ack) + "\n").encode())

                    # Simulate a simple test that makes one generate request
                    request = {
                        "id": 1,
                        "command": "generate",
                        "payload": {"type": "integer"},
                    }
                    conn.sendall((json.dumps(request) + "\n").encode())

                    # Read response
                    response_line = reader.readline()
                    response = json.loads(response_line.decode())
                    assert response.get("id") == 1

                    # Send test result - always fail
                    result_msg = {
                        "type": "test_result",
                        "result": "fail",
                        "message": "assertion failed",
                    }
                    conn.sendall((json.dumps(result_msg) + "\n").encode())

                    conn.close()

                    # Stop after handling the final replay connection
                    if is_last_run:
                        break
                except TimeoutError:
                    break

            server.close()

        server_thread = threading.Thread(target=server_logic)
        server_thread.start()

        import time

        time.sleep(0.2)

        result = subprocess.run(
            ["hegel", "--client-mode", socket_path, "--test-cases", "1", "--no-tui"],
            capture_output=True,
            text=True,
            timeout=30,
        )

        server_thread.join(timeout=10)

        # Should exit with failure code
        assert result.returncode == 1
        # The replay should print the failure message
        assert "Test failed: assertion failed" in result.stderr


def test_client_mode_handles_rejection():
    """Test that client mode handles test rejections correctly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        socket_path = os.path.join(tmpdir, "test.sock")

        # Server that rejects then passes
        def server_logic():
            server = create_server_socket(socket_path)
            server.settimeout(10.0)

            try:
                # First connection: reject
                conn, _ = server.accept()
                reader = conn.makefile("rb")
                reader.readline()  # handshake
                conn.sendall(b'{"type": "handshake_ack"}\n')
                conn.sendall(b'{"type": "test_result", "result": "reject"}\n')
                conn.close()

                # Second connection: pass
                conn, _ = server.accept()
                reader = conn.makefile("rb")
                reader.readline()  # handshake
                conn.sendall(b'{"type": "handshake_ack"}\n')
                conn.sendall(b'{"type": "test_result", "result": "pass"}\n')
                conn.close()
            except TimeoutError:
                pass  # Expected if hegel finishes early

            server.close()

        server_thread = threading.Thread(target=server_logic)
        server_thread.start()

        import time

        time.sleep(0.1)

        result = subprocess.run(
            ["hegel", "--client-mode", socket_path, "--test-cases", "2", "--no-tui"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        server_thread.join(timeout=5)

        # Should pass (rejection is treated as invalid, not failure)
        assert result.returncode == 0


def test_client_mode_requires_socket_path():
    """Test that client mode requires --client-mode argument."""
    result = subprocess.run(
        ["hegel", "--no-tui"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "Either TEST argument or --client-mode is required" in result.stderr


def test_client_mode_handles_span_commands():
    """Test that client mode handles start_span and stop_span commands."""
    with tempfile.TemporaryDirectory() as tmpdir:
        socket_path = os.path.join(tmpdir, "test.sock")

        def server_logic():
            server = create_server_socket(socket_path)
            server.settimeout(10.0)

            try:
                conn, _ = server.accept()
                reader = conn.makefile("rb")

                # Read handshake
                reader.readline()  # handshake
                conn.sendall(b'{"type": "handshake_ack"}\n')

                # Send start_span command
                conn.sendall(
                    b'{"id": 1, "command": "start_span", "payload": {"label": 1}}\n'
                )
                response = reader.readline()
                assert b'"id": 1' in response

                # Send generate command
                conn.sendall(
                    b'{"id": 2, "command": "generate", "payload": {"type": "integer"}}\n'
                )
                response = reader.readline()
                assert b'"id": 2' in response

                # Send stop_span command
                conn.sendall(
                    b'{"id": 3, "command": "stop_span", "payload": {"discard": false}}\n'
                )
                response = reader.readline()
                assert b'"id": 3' in response

                # Send pass result
                conn.sendall(b'{"type": "test_result", "result": "pass"}\n')
                conn.close()
            except TimeoutError:
                pass

            server.close()

        server_thread = threading.Thread(target=server_logic)
        server_thread.start()

        import time

        time.sleep(0.1)

        result = subprocess.run(
            ["hegel", "--client-mode", socket_path, "--test-cases", "1", "--no-tui"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        server_thread.join(timeout=5)
        assert result.returncode == 0


def test_client_mode_with_debug_verbosity():
    """Test that client mode works with --verbosity debug."""
    with tempfile.TemporaryDirectory() as tmpdir:
        socket_path = os.path.join(tmpdir, "test.sock")

        server_thread = threading.Thread(
            target=run_simple_test_server,
            args=(socket_path, [("pass", None)]),
        )
        server_thread.start()

        import time

        time.sleep(0.1)

        result = subprocess.run(
            [
                "hegel",
                "--client-mode",
                socket_path,
                "--test-cases",
                "1",
                "--no-tui",
                "--verbosity",
                "debug",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )

        server_thread.join(timeout=5)
        assert result.returncode == 0
        # Debug verbosity should show handshake message
        assert "Handshake complete" in result.stderr


def test_client_mode_handles_invalid_json():
    """Test that client mode handles invalid JSON gracefully."""
    with tempfile.TemporaryDirectory() as tmpdir:
        socket_path = os.path.join(tmpdir, "test.sock")

        def server_logic():
            server = create_server_socket(socket_path)
            server.settimeout(10.0)

            try:
                conn, _ = server.accept()
                reader = conn.makefile("rb")

                # Read handshake
                reader.readline()
                conn.sendall(b'{"type": "handshake_ack"}\n')

                # Send invalid JSON - should be handled gracefully
                conn.sendall(b"not valid json\n")

                # Then send valid pass result
                conn.sendall(b'{"type": "test_result", "result": "pass"}\n')
                conn.close()
            except TimeoutError:
                pass

            server.close()

        server_thread = threading.Thread(target=server_logic)
        server_thread.start()

        import time

        time.sleep(0.1)

        result = subprocess.run(
            ["hegel", "--client-mode", socket_path, "--test-cases", "1", "--no-tui"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        server_thread.join(timeout=5)
        # Should still succeed since we sent pass result
        assert result.returncode == 0


def test_client_mode_handles_connection_error():
    """Test that client mode handles connection errors gracefully.

    When the socket doesn't exist, client mode treats each connection
    failure as an invalid test case. Eventually hegel gives up and
    exits with success (since no failures were found).
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        socket_path = os.path.join(tmpdir, "nonexistent.sock")

        # Try to connect to a socket that doesn't exist
        result = subprocess.run(
            ["hegel", "--client-mode", socket_path, "--test-cases", "1", "--no-tui"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        # Connection failure is printed but treated as invalid test case
        assert "Failed to connect" in result.stderr


def test_client_mode_handles_unknown_command():
    """Test that client mode returns error for unknown commands."""
    with tempfile.TemporaryDirectory() as tmpdir:
        socket_path = os.path.join(tmpdir, "test.sock")

        def server_logic():
            server = create_server_socket(socket_path)
            server.settimeout(10.0)

            try:
                conn, _ = server.accept()
                reader = conn.makefile("rb")

                # Read handshake
                reader.readline()
                conn.sendall(b'{"type": "handshake_ack"}\n')

                # Send unknown command
                conn.sendall(
                    b'{"id": 1, "command": "unknown_command", "payload": {}}\n'
                )
                response = reader.readline()
                # Should get error response
                resp_data = json.loads(response.decode())
                assert resp_data.get("id") == 1
                assert "error" in resp_data
                assert "Unknown command" in resp_data["error"]

                # Send pass result
                conn.sendall(b'{"type": "test_result", "result": "pass"}\n')
                conn.close()
            except TimeoutError:
                pass

            server.close()

        server_thread = threading.Thread(target=server_logic)
        server_thread.start()

        import time

        time.sleep(0.1)

        result = subprocess.run(
            ["hegel", "--client-mode", socket_path, "--test-cases", "1", "--no-tui"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        server_thread.join(timeout=5)
        assert result.returncode == 0


def test_client_mode_handles_no_handshake_ack():
    """Test that client mode handles missing handshake_ack gracefully."""
    with tempfile.TemporaryDirectory() as tmpdir:
        socket_path = os.path.join(tmpdir, "test.sock")

        def server_logic():
            server = create_server_socket(socket_path)
            server.settimeout(10.0)

            try:
                conn, _ = server.accept()
                reader = conn.makefile("rb")

                # Read handshake
                reader.readline()
                # Close connection without sending handshake_ack
                conn.close()
            except TimeoutError:
                pass

            server.close()

        server_thread = threading.Thread(target=server_logic)
        server_thread.start()

        import time

        time.sleep(0.1)

        result = subprocess.run(
            ["hegel", "--client-mode", socket_path, "--test-cases", "1", "--no-tui"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        server_thread.join(timeout=5)
        # Should report error but treat as invalid test case
        assert "No handshake_ack received" in result.stderr


def test_client_mode_handles_invalid_handshake_ack():
    """Test that client mode handles invalid JSON in handshake_ack."""
    with tempfile.TemporaryDirectory() as tmpdir:
        socket_path = os.path.join(tmpdir, "test.sock")

        def server_logic():
            server = create_server_socket(socket_path)
            server.settimeout(10.0)

            try:
                conn, _ = server.accept()
                reader = conn.makefile("rb")

                # Read handshake
                reader.readline()
                # Send invalid JSON as handshake_ack
                conn.sendall(b"not valid json\n")
                conn.close()
            except TimeoutError:
                pass

            server.close()

        server_thread = threading.Thread(target=server_logic)
        server_thread.start()

        import time

        time.sleep(0.1)

        result = subprocess.run(
            ["hegel", "--client-mode", socket_path, "--test-cases", "1", "--no-tui"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        server_thread.join(timeout=5)
        # Should report error but treat as invalid test case
        assert "Invalid handshake_ack" in result.stderr


def test_client_mode_handles_connection_closed_during_requests():
    """Test that client mode handles connection closed unexpectedly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        socket_path = os.path.join(tmpdir, "test.sock")

        def server_logic():
            server = create_server_socket(socket_path)
            server.settimeout(10.0)

            try:
                conn, _ = server.accept()
                reader = conn.makefile("rb")

                # Read handshake
                reader.readline()
                conn.sendall(b'{"type": "handshake_ack"}\n')

                # Close connection unexpectedly without sending test_result
                conn.close()
            except TimeoutError:
                pass

            server.close()

        server_thread = threading.Thread(target=server_logic)
        server_thread.start()

        import time

        time.sleep(0.1)

        result = subprocess.run(
            ["hegel", "--client-mode", socket_path, "--test-cases", "1", "--no-tui"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        server_thread.join(timeout=5)
        # Should exit successfully (connection closed is not a failure)
        assert result.returncode == 0


def test_client_mode_handles_hypothesis_stoptest():
    """Test that client mode sends reject response when hypothesis raises StopTest.

    StopTest is raised when hypothesis's internal data buffer is exhausted
    during a generate request (e.g., too many draws in one test case).
    The SDK should receive a reject response with reject=true.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        socket_path = os.path.join(tmpdir, "test.sock")

        reject_seen = []  # Track if we saw a reject response

        def server_logic():
            server = create_server_socket(socket_path)
            server.settimeout(15.0)

            # We need to make many generate requests in a single test case
            # to trigger hypothesis's internal data exhaustion (StopTest)
            for _ in range(100):
                try:
                    conn, _ = server.accept()
                    conn.settimeout(10.0)
                    reader = conn.makefile("rb")

                    # Read handshake
                    handshake_line = reader.readline()
                    if not handshake_line:
                        conn.close()
                        continue
                    handshake = json.loads(handshake_line.decode())
                    assert handshake.get("type") == "handshake"

                    # Send handshake_ack
                    conn.sendall(b'{"type": "handshake_ack"}\n')

                    # Make many generate requests in one test case
                    # to exhaust hypothesis's data buffer.
                    # Request very large arrays to quickly use up the buffer.
                    request_id = 0
                    got_reject = False
                    for _ in range(500):  # Many requests to trigger StopTest
                        request_id += 1
                        # Request large arrays that use more of hypothesis's buffer
                        request = {
                            "id": request_id,
                            "command": "generate",
                            "payload": {
                                "type": "array",
                                "items": {"type": "integer"},
                                "minItems": 50,
                                "maxItems": 100,
                            },
                        }
                        conn.sendall((json.dumps(request) + "\n").encode())

                        # Read response
                        response_line = reader.readline()
                        if not response_line:
                            break
                        response = json.loads(response_line.decode())

                        # Check if we got a reject response
                        if response.get("reject"):
                            got_reject = True
                            reject_seen.append(True)
                            break

                    # If we got reject, hegel closed the connection, so move on
                    if got_reject:
                        conn.close()
                        continue

                    # Send test result
                    conn.sendall(b'{"type": "test_result", "result": "pass"}\n')
                    conn.close()
                except (TimeoutError, ConnectionResetError, BrokenPipeError):
                    break

            server.close()

        server_thread = threading.Thread(target=server_logic)
        server_thread.start()

        import time

        time.sleep(0.1)

        # Run with debug verbosity to see reject responses
        result = subprocess.run(
            [
                "hegel",
                "--client-mode",
                socket_path,
                "--test-cases",
                "100",
                "--no-tui",
                "--verbosity",
                "debug",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )

        server_thread.join(timeout=15)

        # We should have seen at least one reject response
        # (hypothesis will eventually exhaust its buffer with large array requests)
        assert len(reject_seen) > 0 or "reject" in result.stderr


def test_client_mode_handles_hypothesis_stoptest_without_debug_verbosity():
    """Test StopTest handling without debug verbosity (for branch coverage)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        socket_path = os.path.join(tmpdir, "test.sock")

        reject_seen = []

        def server_logic():
            server = create_server_socket(socket_path)
            server.settimeout(15.0)

            for _ in range(100):
                try:
                    conn, _ = server.accept()
                    conn.settimeout(10.0)
                    reader = conn.makefile("rb")

                    handshake_line = reader.readline()
                    if not handshake_line:
                        conn.close()
                        continue
                    json.loads(handshake_line.decode())
                    conn.sendall(b'{"type": "handshake_ack"}\n')

                    request_id = 0
                    got_reject = False
                    for _ in range(500):
                        request_id += 1
                        request = {
                            "id": request_id,
                            "command": "generate",
                            "payload": {
                                "type": "array",
                                "items": {"type": "integer"},
                                "minItems": 50,
                                "maxItems": 100,
                            },
                        }
                        conn.sendall((json.dumps(request) + "\n").encode())

                        response_line = reader.readline()
                        if not response_line:
                            break
                        response = json.loads(response_line.decode())

                        if response.get("reject"):
                            got_reject = True
                            reject_seen.append(True)
                            break

                    if got_reject:
                        conn.close()
                        continue

                    conn.sendall(b'{"type": "test_result", "result": "pass"}\n')
                    conn.close()
                except (TimeoutError, ConnectionResetError, BrokenPipeError):
                    break

            server.close()

        server_thread = threading.Thread(target=server_logic)
        server_thread.start()

        import time

        time.sleep(0.1)

        # Run WITHOUT debug flag
        result = subprocess.run(
            [
                "hegel",
                "--client-mode",
                socket_path,
                "--test-cases",
                "100",
                "--no-tui",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )

        server_thread.join(timeout=15)

        # Test should complete without errors
        assert result.returncode == 0 or len(reject_seen) > 0


def test_client_mode_handles_unknown_command_with_debug():
    """Test that client mode returns error for unknown commands with debug enabled.

    This test ensures the debug print statement in the error handling path is covered.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        socket_path = os.path.join(tmpdir, "test.sock")

        def server_logic():
            server = create_server_socket(socket_path)
            server.settimeout(10.0)

            try:
                conn, _ = server.accept()
                reader = conn.makefile("rb")

                # Read handshake
                reader.readline()
                conn.sendall(b'{"type": "handshake_ack"}\n')

                # Send unknown command
                conn.sendall(
                    b'{"id": 1, "command": "unknown_command", "payload": {}}\n'
                )
                response = reader.readline()
                # Should get error response
                resp_data = json.loads(response.decode())
                assert resp_data.get("id") == 1
                assert "error" in resp_data
                assert "Unknown command" in resp_data["error"]

                # Send pass result
                conn.sendall(b'{"type": "test_result", "result": "pass"}\n')
                conn.close()
            except TimeoutError:
                pass

            server.close()

        server_thread = threading.Thread(target=server_logic)
        server_thread.start()

        import time

        time.sleep(0.1)

        result = subprocess.run(
            [
                "hegel",
                "--client-mode",
                socket_path,
                "--test-cases",
                "1",
                "--no-tui",
                "--verbosity",
                "debug",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )

        server_thread.join(timeout=5)
        assert result.returncode == 0
        # Debug verbosity should print the error response
        assert "Unknown command" in result.stderr
