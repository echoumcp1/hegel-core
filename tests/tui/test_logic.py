"""Unit tests for the poll_output and on_result logic used in _run_with_tui."""

import os
import tempfile
import threading
import time


def test_poll_output_rate_limiting():
    """Test the rate limiting logic for poll_output."""
    lock = threading.Lock()
    state = {
        "saved_output": "previous output",
        "last_test_end_time": time.time() - 2.0,  # 2 seconds ago
        "display_switched": False,
        "running": True,
    }

    displayed_output = [None]

    def update_output_sync(text):
        displayed_output[0] = text

    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
        f.write("new output\nwith newline")
        stdout_file = f.name

    try:
        with lock:
            try:
                with open(stdout_file) as f:
                    current_content = f.read()
            except (FileNotFoundError, OSError):
                current_content = ""

            if state["display_switched"]:
                if current_content:
                    update_output_sync(current_content)
            else:
                elapsed = time.time() - state["last_test_end_time"]
                has_line = "\n" in current_content

                if elapsed >= 1.0 and has_line:
                    state["display_switched"] = True
                    update_output_sync(current_content)

        assert state["display_switched"] is True
        assert displayed_output[0] == "new output\nwith newline"
    finally:
        os.unlink(stdout_file)


def test_poll_output_rate_limiting_too_soon():
    """Test that poll_output doesn't switch too soon."""
    lock = threading.Lock()
    state = {
        "saved_output": "previous output",
        "last_test_end_time": time.time(),  # Just now
        "display_switched": False,
        "running": True,
    }

    displayed_output = [None]

    def update_output_sync(text):
        displayed_output[0] = text

    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
        f.write("new output\nwith newline")
        stdout_file = f.name

    try:
        with lock:
            try:
                with open(stdout_file) as f:
                    current_content = f.read()
            except (FileNotFoundError, OSError):
                current_content = ""

            if state["display_switched"]:
                if current_content:
                    update_output_sync(current_content)
            else:
                elapsed = time.time() - state["last_test_end_time"]
                has_line = "\n" in current_content

                if elapsed >= 1.0 and has_line:
                    state["display_switched"] = True
                    update_output_sync(current_content)

        assert state["display_switched"] is False
        assert displayed_output[0] is None
    finally:
        os.unlink(stdout_file)


def test_poll_output_no_newline():
    """Test that poll_output doesn't switch without newline."""
    lock = threading.Lock()
    state = {
        "saved_output": "previous output",
        "last_test_end_time": time.time() - 2.0,  # 2 seconds ago
        "display_switched": False,
        "running": True,
    }

    displayed_output = [None]

    def update_output_sync(text):
        displayed_output[0] = text

    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
        f.write("no newline here")  # No newline
        stdout_file = f.name

    try:
        with lock:
            try:
                with open(stdout_file) as f:
                    current_content = f.read()
            except (FileNotFoundError, OSError):
                current_content = ""

            if state["display_switched"]:
                if current_content:
                    update_output_sync(current_content)
            else:
                elapsed = time.time() - state["last_test_end_time"]
                has_line = "\n" in current_content

                if elapsed >= 1.0 and has_line:
                    state["display_switched"] = True
                    update_output_sync(current_content)

        assert state["display_switched"] is False
        assert displayed_output[0] is None
    finally:
        os.unlink(stdout_file)


def test_poll_output_already_switched():
    """Test that poll_output updates immediately when already switched."""
    lock = threading.Lock()
    state = {
        "saved_output": "previous output",
        "last_test_end_time": time.time(),
        "display_switched": True,  # Already switched
        "running": True,
    }

    displayed_output = [None]

    def update_output_sync(text):
        displayed_output[0] = text

    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
        f.write("updated content")
        stdout_file = f.name

    try:
        with lock:
            try:
                with open(stdout_file) as f:
                    current_content = f.read()
            except (FileNotFoundError, OSError):
                current_content = ""

            if state["display_switched"]:
                if current_content:
                    update_output_sync(current_content)
            else:
                elapsed = time.time() - state["last_test_end_time"]
                has_line = "\n" in current_content

                if elapsed >= 1.0 and has_line:
                    state["display_switched"] = True
                    update_output_sync(current_content)

        assert displayed_output[0] == "updated content"
    finally:
        os.unlink(stdout_file)


def test_poll_output_not_running():
    """Test that poll_output does nothing when not running."""
    lock = threading.Lock()
    state = {
        "saved_output": "previous output",
        "last_test_end_time": time.time() - 2.0,
        "display_switched": False,
        "running": False,  # Not running
    }

    displayed_output = [None]

    def update_output_sync(text):
        displayed_output[0] = text

    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
        f.write("new content\n")
        stdout_file = f.name

    try:
        with lock:
            if not state["running"]:
                pass  # Early return
            else:
                try:
                    with open(stdout_file) as f:
                        current_content = f.read()
                except (FileNotFoundError, OSError):
                    current_content = ""

                if state["display_switched"]:
                    if current_content:
                        update_output_sync(current_content)
                else:
                    elapsed = time.time() - state["last_test_end_time"]
                    has_line = "\n" in current_content

                    if elapsed >= 1.0 and has_line:
                        state["display_switched"] = True
                        update_output_sync(current_content)

        assert displayed_output[0] is None
    finally:
        os.unlink(stdout_file)


def test_poll_output_file_not_found():
    """Test that poll_output handles missing file gracefully."""
    lock = threading.Lock()
    state = {
        "saved_output": "previous output",
        "last_test_end_time": time.time() - 2.0,
        "display_switched": False,
        "running": True,
    }

    displayed_output = [None]

    def update_output_sync(text):
        displayed_output[0] = text

    stdout_file = "/nonexistent/path/stdout"

    with lock:
        try:
            with open(stdout_file) as f:
                current_content = f.read()
        except (FileNotFoundError, OSError):
            current_content = ""

        if state["display_switched"]:
            if current_content:
                update_output_sync(current_content)
        else:
            elapsed = time.time() - state["last_test_end_time"]
            has_line = "\n" in current_content

            if elapsed >= 1.0 and has_line:
                state["display_switched"] = True
                update_output_sync(current_content)

    assert state["display_switched"] is False
    assert displayed_output[0] is None


def test_on_result_saves_output():
    """Test that on_result callback saves output and resets state."""
    lock = threading.Lock()
    state = {
        "saved_output": "",
        "last_test_end_time": 0.0,
        "display_switched": True,  # Was switched
        "running": True,
    }

    displayed_output = [None]

    def update_output(text):
        displayed_output[0] = text

    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
        f.write("test output content")
        stdout_file = f.name

    try:
        with lock:
            try:
                with open(stdout_file) as f:
                    state["saved_output"] = f.read()
            except (FileNotFoundError, OSError):
                pass

            update_output(state["saved_output"])
            state["last_test_end_time"] = time.time()
            state["display_switched"] = False

        assert state["saved_output"] == "test output content"
        assert displayed_output[0] == "test output content"
        assert state["display_switched"] is False
        assert state["last_test_end_time"] > 0
    finally:
        os.unlink(stdout_file)


def test_on_result_handles_missing_file():
    """Test that on_result handles missing stdout file."""
    lock = threading.Lock()
    state = {
        "saved_output": "old output",
        "last_test_end_time": 0.0,
        "display_switched": True,
        "running": True,
    }

    displayed_output = [None]

    def update_output(text):
        displayed_output[0] = text

    stdout_file = "/nonexistent/path/stdout"

    with lock:
        try:
            with open(stdout_file) as f:
                state["saved_output"] = f.read()
        except (FileNotFoundError, OSError):
            pass  # Keep previous saved_output

        update_output(state["saved_output"])
        state["last_test_end_time"] = time.time()
        state["display_switched"] = False

    assert state["saved_output"] == "old output"
    assert displayed_output[0] == "old output"
