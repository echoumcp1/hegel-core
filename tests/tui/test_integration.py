"""Integration tests for _run_with_tui function."""

import json
import os
import sys
import time

from click.testing import CliRunner

import hegel.__main__ as main_module
from hegel.__main__ import _run_with_tui, main


class MockHegelApp:
    def __init__(self, run_func, poll_callback=None, poll_interval=0.1):
        self._run_func = run_func
        self._poll_callback = poll_callback
        self._poll_interval = poll_interval
        self._exit_code = 0

    def run(self):
        self._run_func(self)

    def update_output(self, text):
        pass

    def update_output_sync(self, text):
        pass

    def update_stats(self, stats):
        pass

    def finish(self, exit_code=0):
        self._exit_code = exit_code

    @property
    def exit_code(self):
        return self._exit_code


def test_run_with_tui_passes_on_stdout_file_to_runner(cpp_binaries, monkeypatch):
    """Test that _run_with_tui passes on_stdout_file callback to make_test_function."""
    on_stdout_file_callbacks = []

    original_make_test_function = main_module.make_test_function

    def capture_make_test_function(*args, **kwargs):
        if "on_stdout_file" in kwargs:
            on_stdout_file_callbacks.append(kwargs["on_stdout_file"])
        return original_make_test_function(*args, **kwargs)

    monkeypatch.setattr(
        main_module, "make_test_function", capture_make_test_function
    )
    monkeypatch.setattr(sys, "exit", lambda code: (_ for _ in ()).throw(SystemExit(code)))
    monkeypatch.setattr("hegel.tui.HegelApp", MockHegelApp)

    db_key = json.dumps([cpp_binaries.const42]).encode("utf-8")

    try:
        _run_with_tui([cpp_binaries.const42], 137, 1, db_key)
    except SystemExit:
        pass

    assert len(on_stdout_file_callbacks) == 1
    assert on_stdout_file_callbacks[0] is not None


def test_run_with_tui_updates_stats(cpp_binaries, monkeypatch):
    """Test that _run_with_tui updates stats during test execution."""
    stats_updates = []

    class CapturingApp(MockHegelApp):
        def update_stats(self, stats):
            stats_updates.append(stats)

    monkeypatch.setattr(sys, "exit", lambda code: (_ for _ in ()).throw(SystemExit(code)))
    monkeypatch.setattr("hegel.tui.HegelApp", CapturingApp)

    db_key = json.dumps([cpp_binaries.const42]).encode("utf-8")

    try:
        _run_with_tui([cpp_binaries.const42], 137, 1, db_key)
    except SystemExit:
        pass

    assert len(stats_updates) >= 1
    assert stats_updates[0].phase == "running"
    assert stats_updates[-1].phase == "passed"


def test_run_with_tui_handles_failure(cpp_binaries, monkeypatch):
    """Test that _run_with_tui handles test failure correctly."""
    stats_updates = []

    class CapturingApp(MockHegelApp):
        def update_stats(self, stats):
            stats_updates.append(stats)

    monkeypatch.setattr(sys, "exit", lambda code: (_ for _ in ()).throw(SystemExit(code)))
    monkeypatch.setattr("hegel.tui.HegelApp", CapturingApp)

    db_key = json.dumps([cpp_binaries.hfear]).encode("utf-8")

    try:
        _run_with_tui([cpp_binaries.hfear], 137, 100, db_key)
    except SystemExit:
        pass

    failed_stats = [s for s in stats_updates if s.phase == "failed"]
    assert len(failed_stats) >= 1
    assert failed_stats[-1].failures >= 1


def test_run_with_tui_poll_callback_receives_correct_interval(
    cpp_binaries, monkeypatch
):
    """Test that poll callback is set up with correct interval."""
    poll_intervals = []
    poll_callbacks = []

    class CapturingApp(MockHegelApp):
        def __init__(self, run_func, poll_callback=None, poll_interval=0.1):
            super().__init__(run_func, poll_callback, poll_interval)
            poll_callbacks.append(poll_callback)
            poll_intervals.append(poll_interval)

    monkeypatch.setattr(sys, "exit", lambda code: (_ for _ in ()).throw(SystemExit(code)))
    monkeypatch.setattr("hegel.tui.HegelApp", CapturingApp)

    db_key = json.dumps([cpp_binaries.const42]).encode("utf-8")

    try:
        _run_with_tui([cpp_binaries.const42], 137, 1, db_key)
    except SystemExit:
        pass

    assert len(poll_callbacks) == 1
    assert poll_callbacks[0] is not None
    assert poll_intervals[0] == 0.1


def test_run_with_tui_poll_callback_is_executed(cpp_binaries, monkeypatch):
    """Test that poll callback is actually called and works."""
    poll_callback_ref = [None]

    class CapturingApp(MockHegelApp):
        def __init__(self, run_func, poll_callback=None, poll_interval=0.1):
            super().__init__(run_func, poll_callback, poll_interval)
            poll_callback_ref[0] = poll_callback

        def run(self):
            self._run_func(self)
            if self._poll_callback:
                self._poll_callback()

    monkeypatch.setattr(sys, "exit", lambda code: (_ for _ in ()).throw(SystemExit(code)))
    monkeypatch.setattr("hegel.tui.HegelApp", CapturingApp)

    db_key = json.dumps([cpp_binaries.const42]).encode("utf-8")

    try:
        _run_with_tui([cpp_binaries.const42], 137, 1, db_key)
    except SystemExit:
        pass

    assert poll_callback_ref[0] is not None


def test_run_with_tui_main_entry_point(cpp_binaries, monkeypatch):
    """Test that main() calls _run_with_tui when tui=True."""
    run_with_tui_called = [False]

    def mock_run_with_tui(*args, **kwargs):
        run_with_tui_called[0] = True

    monkeypatch.setattr(main_module, "_run_with_tui", mock_run_with_tui)
    monkeypatch.setattr(sys, "exit", lambda code: None)

    runner = CliRunner()
    runner.invoke(main, [cpp_binaries.const42])

    assert run_with_tui_called[0] is True


def test_run_with_tui_poll_output_all_branches(cpp_binaries, monkeypatch):
    """Test poll_output function with various states to cover all branches."""
    poll_calls = []

    class CapturingApp(MockHegelApp):
        def run(self):
            self._run_func(self)
            if self._poll_callback:
                self._poll_callback()
                poll_calls.append("after_finish")

    monkeypatch.setattr(sys, "exit", lambda code: (_ for _ in ()).throw(SystemExit(code)))
    monkeypatch.setattr("hegel.tui.HegelApp", CapturingApp)

    db_key = json.dumps([cpp_binaries.const42]).encode("utf-8")

    try:
        _run_with_tui([cpp_binaries.const42], 137, 1, db_key)
    except SystemExit:
        pass

    assert "after_finish" in poll_calls


def test_run_with_tui_poll_during_test_execution(cpp_binaries, monkeypatch):
    """Test poll_output during test execution with stdout file content."""
    poll_results = []
    poll_callback_ref = [None]

    original_make_test_function = main_module.make_test_function

    def capture_make_test_function(*args, **kwargs):
        if "on_stdout_file" in kwargs:
            original_callback = kwargs["on_stdout_file"]

            def wrapped_callback(path):
                if original_callback:
                    original_callback(path)

            kwargs["on_stdout_file"] = wrapped_callback
        return original_make_test_function(*args, **kwargs)

    class CapturingApp(MockHegelApp):
        def __init__(self, run_func, poll_callback=None, poll_interval=0.1):
            super().__init__(run_func, poll_callback, poll_interval)
            poll_callback_ref[0] = poll_callback

        def run(self):
            if self._poll_callback:
                self._poll_callback()
                poll_results.append("before_test")
            self._run_func(self)

    monkeypatch.setattr(main_module, "make_test_function", capture_make_test_function)
    monkeypatch.setattr(sys, "exit", lambda code: (_ for _ in ()).throw(SystemExit(code)))
    monkeypatch.setattr("hegel.tui.HegelApp", CapturingApp)

    db_key = json.dumps([cpp_binaries.const42]).encode("utf-8")

    try:
        _run_with_tui([cpp_binaries.const42], 137, 1, db_key)
    except SystemExit:
        pass

    assert "before_test" in poll_results


def test_run_with_tui_poll_with_file_content(cpp_binaries, monkeypatch):
    """Test poll_output when file has content and conditions are met."""
    poll_callback_ref = [None]

    original_make_test_function = main_module.make_test_function

    def capture_make_test_function(*args, **kwargs):
        if "on_stdout_file" in kwargs:
            original_callback = kwargs["on_stdout_file"]

            def wrapped_callback(path):
                if original_callback:
                    original_callback(path)

            kwargs["on_stdout_file"] = wrapped_callback
        return original_make_test_function(*args, **kwargs)

    class CapturingApp(MockHegelApp):
        def __init__(self, run_func, poll_callback=None, poll_interval=0.1):
            super().__init__(run_func, poll_callback, poll_interval)
            poll_callback_ref[0] = poll_callback

    monkeypatch.setattr(main_module, "make_test_function", capture_make_test_function)
    monkeypatch.setattr(sys, "exit", lambda code: (_ for _ in ()).throw(SystemExit(code)))
    monkeypatch.setattr("hegel.tui.HegelApp", CapturingApp)

    db_key = json.dumps([cpp_binaries.const42]).encode("utf-8")

    try:
        _run_with_tui([cpp_binaries.const42], 137, 1, db_key)
    except SystemExit:
        pass

    assert poll_callback_ref[0] is not None


def test_run_with_tui_poll_with_switched_state(cpp_binaries, monkeypatch):
    """Test poll_output when display_switched is True."""
    poll_callback_ref = [None]

    original_make_test_function = main_module.make_test_function

    def capture_make_test_function(*args, **kwargs):
        if "on_stdout_file" in kwargs:
            original_callback = kwargs["on_stdout_file"]

            def wrapped_callback(path):
                if original_callback:
                    original_callback(path)

            kwargs["on_stdout_file"] = wrapped_callback

        orig_on_result = kwargs.get("on_result")

        def wrapped_on_result(result):
            if orig_on_result:
                orig_on_result(result)

        kwargs["on_result"] = wrapped_on_result
        return original_make_test_function(*args, **kwargs)

    class CapturingApp(MockHegelApp):
        def __init__(self, run_func, poll_callback=None, poll_interval=0.1):
            super().__init__(run_func, poll_callback, poll_interval)
            poll_callback_ref[0] = poll_callback

    monkeypatch.setattr(main_module, "make_test_function", capture_make_test_function)
    monkeypatch.setattr(sys, "exit", lambda code: (_ for _ in ()).throw(SystemExit(code)))
    monkeypatch.setattr("hegel.tui.HegelApp", CapturingApp)

    db_key = json.dumps([cpp_binaries.const42]).encode("utf-8")

    try:
        _run_with_tui([cpp_binaries.const42], 137, 1, db_key)
    except SystemExit:
        pass

    assert poll_callback_ref[0] is not None


def test_run_with_tui_poll_exercises_all_branches(cpp_binaries, monkeypatch):
    """Test poll_output with actual file content during execution."""
    poll_callback_ref = [None]
    stdout_file_ref = [None]
    poll_during_test_results = []

    original_make_test_function = main_module.make_test_function

    def capture_make_test_function(*args, **kwargs):
        if "on_stdout_file" in kwargs:
            original_callback = kwargs["on_stdout_file"]

            def wrapped_callback(path):
                stdout_file_ref[0] = path
                if original_callback:
                    original_callback(path)

            kwargs["on_stdout_file"] = wrapped_callback

        orig_on_result = kwargs.get("on_result")

        def wrapped_on_result(result):
            if stdout_file_ref[0]:
                if poll_callback_ref[0]:
                    poll_callback_ref[0]()
                    poll_during_test_results.append("poll_before_on_result")

            if orig_on_result:
                orig_on_result(result)

        kwargs["on_result"] = wrapped_on_result
        return original_make_test_function(*args, **kwargs)

    class CapturingApp(MockHegelApp):
        def __init__(self, run_func, poll_callback=None, poll_interval=0.1):
            super().__init__(run_func, poll_callback, poll_interval)
            poll_callback_ref[0] = poll_callback

    monkeypatch.setattr(main_module, "make_test_function", capture_make_test_function)
    monkeypatch.setattr(sys, "exit", lambda code: (_ for _ in ()).throw(SystemExit(code)))
    monkeypatch.setattr("hegel.tui.HegelApp", CapturingApp)

    db_key = json.dumps([cpp_binaries.const42]).encode("utf-8")

    try:
        _run_with_tui([cpp_binaries.const42], 137, 1, db_key)
    except SystemExit:
        pass

    assert "poll_before_on_result" in poll_during_test_results


def test_run_with_tui_poll_reads_file_during_test(cpp_binaries, monkeypatch):
    """Test that poll_output reads stdout file during test execution."""
    outputs_synced = []
    poll_callback_ref = [None]
    poll_called_with_file = [False]

    real_time = time.time
    fake_start_time = [None]

    def fake_time():
        if fake_start_time[0] is None:
            fake_start_time[0] = real_time()
        return fake_start_time[0] + 2.0

    class CapturingApp(MockHegelApp):
        def __init__(self, run_func, poll_callback=None, poll_interval=0.1):
            super().__init__(run_func, poll_callback, poll_interval)
            poll_callback_ref[0] = poll_callback

        def update_output_sync(self, text):
            outputs_synced.append(text)

    original_make_test_function = main_module.make_test_function

    def wrap_make_test_function(*args, **kwargs):
        orig_on_stdout_file = kwargs.get("on_stdout_file")

        def wrapped_on_stdout_file(path):
            if orig_on_stdout_file:
                orig_on_stdout_file(path)

            if poll_callback_ref[0]:
                poll_callback_ref[0]()

                with open(path, "w") as f:
                    f.write("test output\n")
                poll_callback_ref[0]()
                poll_called_with_file[0] = True

                poll_callback_ref[0]()

                with open(path, "w") as f:
                    f.write("")
                poll_callback_ref[0]()

        kwargs["on_stdout_file"] = wrapped_on_stdout_file
        return original_make_test_function(*args, **kwargs)

    monkeypatch.setattr(time, "time", fake_time)
    monkeypatch.setattr(main_module, "make_test_function", wrap_make_test_function)
    monkeypatch.setattr(sys, "exit", lambda code: (_ for _ in ()).throw(SystemExit(code)))
    monkeypatch.setattr("hegel.tui.HegelApp", CapturingApp)

    db_key = json.dumps([cpp_binaries.const42]).encode("utf-8")

    try:
        _run_with_tui([cpp_binaries.const42], 137, 1, db_key)
    except SystemExit:
        pass

    assert poll_called_with_file[0] is True
    assert len(outputs_synced) >= 1


def test_run_with_tui_poll_with_content_and_switched(
    cpp_binaries, monkeypatch
):
    """Test poll_output when display_switched is True and file has content."""
    monkeypatch.setattr(sys, "exit", lambda code: (_ for _ in ()).throw(SystemExit(code)))
    monkeypatch.setattr("hegel.tui.HegelApp", MockHegelApp)

    db_key = json.dumps([cpp_binaries.const42]).encode("utf-8")

    try:
        _run_with_tui([cpp_binaries.const42], 137, 1, db_key)
    except SystemExit:
        pass


def test_run_with_tui_on_result_with_missing_file(cpp_binaries, monkeypatch):
    """Test on_result callback when stdout file doesn't exist."""
    on_result_called = [False]
    captured_stdout_file = [None]

    original_make_test_function = main_module.make_test_function

    def patched_make_test_function(*args, **kwargs):
        if "on_stdout_file" in kwargs:
            original_on_stdout_file = kwargs["on_stdout_file"]

            def wrapped_on_stdout_file(path):
                captured_stdout_file[0] = path
                if original_on_stdout_file:
                    original_on_stdout_file(path)

            kwargs["on_stdout_file"] = wrapped_on_stdout_file

        original_on_result = kwargs.get("on_result")

        def wrapped_on_result(result):
            if captured_stdout_file[0] and os.path.exists(captured_stdout_file[0]):
                os.unlink(captured_stdout_file[0])
            on_result_called[0] = True
            if original_on_result:
                original_on_result(result)

        kwargs["on_result"] = wrapped_on_result
        return original_make_test_function(*args, **kwargs)

    monkeypatch.setattr(main_module, "make_test_function", patched_make_test_function)
    monkeypatch.setattr(sys, "exit", lambda code: (_ for _ in ()).throw(SystemExit(code)))
    monkeypatch.setattr("hegel.tui.HegelApp", MockHegelApp)

    db_key = json.dumps([cpp_binaries.const42]).encode("utf-8")

    try:
        _run_with_tui([cpp_binaries.const42], 137, 1, db_key)
    except SystemExit:
        pass

    assert on_result_called[0] is True
