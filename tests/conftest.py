"""Pytest configuration for hegel tests.

Note: Some tests (test_main.py, test_runner.py, test_tui.py) require C++ test
binaries which are built from the hegel-cpp repository. These tests will be
skipped if the binaries are not available.
"""

from dataclasses import dataclass
from pathlib import Path

import pytest


@dataclass
class CppTestBinaries:
    """Paths to compiled C++ test binaries."""

    hfear: str
    const42: str
    reject: str
    hello3: str
    hello_slow: str


@pytest.fixture(scope="session")
def cpp_binaries() -> CppTestBinaries:
    """Return paths to C++ test binaries.

    This fixture requires special C++ test programs that are built from
    the hegel-cpp repository's test fixtures. Tests using this fixture
    will be skipped if binaries are not available.
    """
    # Look for test binaries in common locations
    possible_paths = [
        Path.home() / "Desktop/coding/hegel-cpp/build/test_fixtures",
        Path(__file__).parent.parent.parent / "hegel-cpp/build/test_fixtures",
    ]

    for fixtures_dir in possible_paths:
        hfear = fixtures_dir / "hfear"
        const42 = fixtures_dir / "const42"
        reject = fixtures_dir / "reject"
        hello3 = fixtures_dir / "hello3"
        hello_slow = fixtures_dir / "hello_slow"

        if all(p.exists() for p in [hfear, const42, reject, hello3, hello_slow]):
            return CppTestBinaries(
                hfear=str(hfear),
                const42=str(const42),
                reject=str(reject),
                hello3=str(hello3),
                hello_slow=str(hello_slow),
            )

    pytest.skip(
        "C++ test binaries not found. These tests require special test fixtures "
        "from hegel-cpp. Run independent tests with: pytest tests/test_client_mode.py "
        "tests/test_process_management.py tests/test_schema_processing.py"
    )
