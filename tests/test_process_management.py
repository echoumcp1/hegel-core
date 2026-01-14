import signal
import subprocess
import sys


from hegel.runner import interrupt_wait_and_kill


def test_does_nothing_if_process_already_exited():
    sp = subprocess.Popen(["true"], start_new_session=True)
    sp.wait()
    assert sp.returncode is not None
    interrupt_wait_and_kill(sp)  # Should return immediately without error


def test_kills_process_that_exits_on_sigint():
    sp = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        start_new_session=True,
    )
    assert sp.returncode is None
    interrupt_wait_and_kill(sp, delay=0.01)
    assert sp.returncode is not None


def test_kills_process_that_ignores_sigint():
    sp = subprocess.Popen(
        [
            sys.executable,
            "-c",
            """
import signal, time, sys
signal.signal(signal.SIGINT, lambda *args: None)
print("ready", flush=True)
time.sleep(10)
""",
        ],
        stdout=subprocess.PIPE,
        start_new_session=True,
    )
    sp.stdout.readline()  # Wait for "ready"
    assert sp.returncode is None
    interrupt_wait_and_kill(sp, delay=0.01)
    assert sp.returncode is not None
    assert sp.returncode == -signal.SIGKILL


def test_closes_pipes():
    sp = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    interrupt_wait_and_kill(sp, delay=0.01)
    assert sp.stdin.closed
    assert sp.stdout.closed
    assert sp.stderr.closed


def test_handles_process_without_pipes():
    sp = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        start_new_session=True,
    )
    assert sp.stdin is None
    assert sp.stdout is None
    assert sp.stderr is None
    interrupt_wait_and_kill(sp, delay=0.01)
    assert sp.returncode is not None


def test_handles_process_that_exits_during_polling():
    sp = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(0.05)"],
        start_new_session=True,
    )
    interrupt_wait_and_kill(sp, delay=0.01)
    assert sp.returncode is not None


def test_handles_partially_closed_pipes():
    sp = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        stdout=subprocess.PIPE,
        start_new_session=True,
    )
    sp.stdout.close()
    interrupt_wait_and_kill(sp, delay=0.01)
    assert sp.returncode is not None
