import json
import os
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hypothesis import given, settings, strategies as st

INT32_MIN = -(2**31)
INT32_MAX = 2**31 - 1


@dataclass
class ConformanceTest:
    params_strategy: st.SearchStrategy[dict[str, Any]]
    validate: Callable[[dict[str, Any], dict[str, Any]], None] | None = None
    # For tests that need to check across all test cases
    aggregate_validate: (
        Callable[[dict[str, Any], list[dict[str, Any]]], None] | None
    ) = None
    # Override test_cases
    test_cases: int | None = None


# =============================================================================
# Parameter Strategies
# =============================================================================


def _booleans_params() -> st.SearchStrategy[dict[str, Any]]:
    return st.just({})


@st.composite
def _integers_params(draw: st.DrawFn) -> dict[str, Any]:
    min_value = draw(st.integers(min_value=INT32_MIN, max_value=INT32_MAX))
    max_value = draw(st.integers(min_value=min_value, max_value=INT32_MAX))
    return {"min_value": min_value, "max_value": max_value}


@st.composite
def _floats_params(draw: st.DrawFn) -> dict[str, Any]:
    min_value = draw(
        st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False)
    )
    max_value = draw(
        st.floats(
            min_value=min_value, max_value=1e6, allow_nan=False, allow_infinity=False
        )
    )
    exclude_min = draw(st.booleans())
    exclude_max = draw(st.booleans())

    # If min == max, can't exclude both
    if min_value == max_value and (exclude_min or exclude_max):
        exclude_min = False
        exclude_max = False

    allow_nan = draw(st.booleans())
    allow_infinity = draw(st.booleans())

    return {
        "min_value": min_value,
        "max_value": max_value,
        "exclude_min": exclude_min,
        "exclude_max": exclude_max,
        "allow_nan": allow_nan,
        "allow_infinity": allow_infinity,
    }


@st.composite
def _text_params(draw: st.DrawFn) -> dict[str, Any]:
    min_length = draw(st.integers(0, 50))
    max_length = draw(st.integers(min_value=min_length, max_value=100))
    return {"min_length": min_length, "max_length": max_length}


@st.composite
def _lists_params(draw: st.DrawFn) -> dict[str, Any]:
    min_size = draw(st.integers(0, 100))
    max_size = draw(st.integers(min_value=min_size, max_value=100))
    min_value = draw(st.integers(min_value=INT32_MIN, max_value=INT32_MAX))
    max_value = draw(st.integers(min_value=min_value, max_value=INT32_MAX))
    return {
        "min_size": min_size,
        "max_size": max_size,
        "min_value": min_value,
        "max_value": max_value,
    }


@st.composite
def _sampled_from_params(draw: st.DrawFn) -> dict[str, Any]:
    options = draw(
        st.lists(st.integers(-1000, 1000), min_size=1, max_size=10, unique=True)
    )
    return {"options": options}


# =============================================================================
# Validators
# =============================================================================


def _validate_booleans(params: dict[str, Any], metrics: dict[str, Any]) -> None:
    assert metrics["value"] in (True, False)


def _validate_integers(params: dict[str, Any], metrics: dict[str, Any]) -> None:
    assert params["min_value"] <= metrics["value"] <= params["max_value"]


def _aggregate_validate_floats(
    params: dict[str, Any], metrics_list: list[dict[str, Any]]
) -> None:
    if params.get("allow_nan"):
        assert any(m.get("is_nan") for m in metrics_list)

    if params.get("allow_infinity"):
        assert any(m.get("is_infinite") for m in metrics_list)

    for metrics in metrics_list:
        if metrics.get("is_nan") or metrics.get("is_infinite"):
            continue

        value = metrics["value"]
        assert value >= params["min_value"]
        assert value <= params["max_value"]
        if params["exclude_min"]:
            assert value != params["min_value"]
        if params["exclude_max"]:
            assert value != params["max_value"]


def _validate_text(params: dict[str, Any], metrics: dict[str, Any]) -> None:
    assert params["min_length"] <= metrics["length"] <= params["max_length"]


def _validate_lists(params: dict[str, Any], metrics: dict[str, Any]) -> None:
    assert params["min_size"] <= metrics["size"] <= params["max_size"]
    if metrics["size"] > 0:
        assert metrics["min_element"] >= params["min_value"]
        assert metrics["max_element"] <= params["max_value"]


def _validate_sampled_from(params: dict[str, Any], metrics: dict[str, Any]) -> None:
    assert metrics["value"] in params["options"]


# =============================================================================
# Test Definitions
# =============================================================================

CONFORMANCE_TESTS: dict[str, ConformanceTest] = {
    "booleans": ConformanceTest(
        params_strategy=_booleans_params(),
        validate=_validate_booleans,
    ),
    "integers": ConformanceTest(
        params_strategy=_integers_params(),
        validate=_validate_integers,
    ),
    "floats": ConformanceTest(
        params_strategy=_floats_params(),
        aggregate_validate=_aggregate_validate_floats,
        test_cases=500,  # NaN/infinity are rare, need more samples
    ),
    "text": ConformanceTest(
        params_strategy=_text_params(),
        validate=_validate_text,
    ),
    "lists": ConformanceTest(
        params_strategy=_lists_params(),
        validate=_validate_lists,
    ),
    "sampled_from": ConformanceTest(
        params_strategy=_sampled_from_params(),
        validate=_validate_sampled_from,
    ),
}


# =============================================================================
# Test Runner
# =============================================================================


def _run_single_test(
    binary: Path,
    params: dict[str, Any],
    test_cases: int,
) -> list[dict[str, Any]]:
    """Run a single conformance test and return list of metrics (one per test case)."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        metrics_file = Path(f.name)

    try:
        # Pass params directly (not wrapped in test/params/test_cases structure)
        input_json = json.dumps(params)

        result = subprocess.run(
            [str(binary), input_json],
            env={
                **os.environ,
                "CONFORMANCE_METRICS_FILE": str(metrics_file),
                "CONFORMANCE_TEST_CASES": str(test_cases),
            },
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"Conformance binary failed with exit code {result.returncode}\n"
                f"stdout: {result.stdout}\n"
                f"stderr: {result.stderr}"
            )

        # Each line is a complete test case with all required metrics
        metrics_list: list[dict[str, Any]] = []
        for line in metrics_file.read_text().splitlines():
            if line.strip():
                metrics_list.append(json.loads(line))
        return metrics_list
    finally:
        metrics_file.unlink(missing_ok=True)


def run_conformance_tests(
    binaries: dict[str, str | Path],
    test_cases: int = 50,
    hypothesis_iterations: int = 5,
) -> None:
    for test_name, binary_path in binaries.items():
        if test_name not in CONFORMANCE_TESTS:
            raise ValueError(
                f"Unknown test: {test_name}. "
                f"Available tests: {list(CONFORMANCE_TESTS.keys())}"
            )

        binary = Path(binary_path)
        if not binary.exists():
            raise FileNotFoundError(f"Conformance binary not found: {binary}")

        test_def = CONFORMANCE_TESTS[test_name]

        effective_test_cases = test_def.test_cases or test_cases

        @settings(max_examples=hypothesis_iterations, deadline=None)
        @given(params=test_def.params_strategy)
        def run_test(params: dict[str, Any]) -> None:
            metrics_list = _run_single_test(binary, params, effective_test_cases)
            if test_def.aggregate_validate:
                test_def.aggregate_validate(params, metrics_list)
            elif test_def.validate:
                for metrics in metrics_list:
                    test_def.validate(params, metrics)

        run_test()
