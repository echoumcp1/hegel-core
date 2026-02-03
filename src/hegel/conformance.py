import json
import os
import subprocess
import tempfile
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

import pytest
from hypothesis import given, settings as Settings, strategies as st


@st.composite
def _integer_params_strategy(
    draw: st.DrawFn,
    min_value: int | None,
    max_value: int | None,
) -> dict[str, Any]:
    """Generate random integer bounds within the given constraints."""
    drawn_min = min_value
    drawn_max = max_value

    use_min = draw(st.booleans())
    use_max = draw(st.booleans())

    if min_value is not None and use_min:
        drawn_min = draw(st.integers(min_value=min_value, max_value=max_value))
    if max_value is not None and use_max:
        lower = drawn_min if drawn_min is not None else min_value
        drawn_max = draw(st.integers(min_value=lower, max_value=max_value))

    return {"min_value": drawn_min, "max_value": drawn_max}


class ConformanceTest(ABC):
    """Base class for SDK conformance tests."""

    default_test_cases: int = 50
    registered_tests: ClassVar[set[type["ConformanceTest"]]] = set()

    def __init_subclass__(cls) -> None:
        cls.registered_tests.add(cls)

    def __init__(
        self,
        binary_path: str | Path,
        test_cases: int | None = None,
    ) -> None:
        self.binary = Path(binary_path)
        assert self.binary.exists()
        self.test_cases = test_cases or self.default_test_cases

    @abstractmethod
    def params_strategy(self) -> st.SearchStrategy[dict[str, Any]]:
        """Return a strategy for generating test parameters."""
        ...

    @abstractmethod
    def validate(
        self,
        metrics_list: list[dict[str, Any]],
        params: dict[str, Any],
    ) -> None:
        """Validate that the SDK output matches the expected constraints."""
        ...

    def run(self, params: dict[str, Any]) -> None:
        """Run the conformance binary and validate its output."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl") as f:
            metrics_file = Path(f.name)
            input_json = json.dumps(params)

            result = subprocess.run(
                [str(self.binary), input_json],
                env={
                    **os.environ,
                    "CONFORMANCE_METRICS_FILE": str(metrics_file),
                    "CONFORMANCE_TEST_CASES": str(self.test_cases),
                },
                capture_output=True,
                text=True,
            )

            if result.returncode != 0:
                raise RuntimeError(
                    f"Conformance binary failed with exit code {result.returncode}\n"
                    f"stdout: {result.stdout}\n"
                    f"stderr: {result.stderr}",
                )

            metrics_list = [
                json.loads(line) for line in metrics_file.read_text().splitlines()
            ]

        self.validate(metrics_list, params)


class BooleanConformance(ConformanceTest):
    """Conformance test for boolean generation."""

    def params_strategy(self) -> st.SearchStrategy[dict[str, Any]]:
        return st.just({})

    def validate(
        self,
        metrics_list: list[dict[str, Any]],
        params: dict[str, Any],
    ) -> None:
        for metrics in metrics_list:
            assert metrics["value"] in (True, False)


class IntegerConformance(ConformanceTest):
    """Conformance test for integer generation with bounds."""

    def __init__(
        self,
        binary_path: str | Path,
        test_cases: int | None = None,
        *,
        min_value: int | None = None,
        max_value: int | None = None,
    ) -> None:
        super().__init__(binary_path, test_cases)
        self.min_value = min_value
        self.max_value = max_value

    def params_strategy(self) -> st.SearchStrategy[dict[str, Any]]:
        return _integer_params_strategy(self.min_value, self.max_value)

    def validate(
        self,
        metrics_list: list[dict[str, Any]],
        params: dict[str, Any],
    ) -> None:
        for metrics in metrics_list:
            value = metrics["value"]
            if params["min_value"] is not None:
                assert value >= params["min_value"]
            if params["max_value"] is not None:
                assert value <= params["max_value"]


class FloatConformance(ConformanceTest):
    """Conformance test for float generation with bounds and special values."""

    default_test_cases = 500  # NaN/infinity are rare, need more samples

    def params_strategy(self) -> st.SearchStrategy[dict[str, Any]]:
        @st.composite
        def strategy(draw: st.DrawFn) -> dict[str, Any]:
            use_min_value = draw(st.booleans())
            use_max_value = draw(st.booleans())

            min_value = None
            max_value = None

            if use_min_value:
                min_value = draw(
                    st.floats(
                        min_value=-1e6,
                        max_value=1e6,
                        allow_nan=False,
                        allow_infinity=False,
                    ),
                )

            if use_max_value:
                min_val = min_value if min_value is not None else -1e6
                max_value = draw(
                    st.floats(
                        min_value=min_val,
                        max_value=1e6,
                        allow_nan=False,
                        allow_infinity=False,
                    ),
                )

            # exclude_min/max only meaningful with bounds
            exclude_min = draw(st.booleans()) if use_min_value else False
            exclude_max = draw(st.booleans()) if use_max_value else False

            # Can't exclude both when min == max
            if (
                min_value is not None
                and max_value is not None
                and min_value == max_value
            ):
                exclude_min = False
                exclude_max = False

            allow_nan = (
                False if (use_min_value or use_max_value) else draw(st.booleans())
            )
            allow_infinity = (
                False if (use_min_value and use_max_value) else draw(st.booleans())
            )

            return {
                "min_value": min_value,
                "max_value": max_value,
                "exclude_min": exclude_min,
                "exclude_max": exclude_max,
                "allow_nan": allow_nan,
                "allow_infinity": allow_infinity,
            }

        return strategy()

    def validate(
        self,
        metrics_list: list[dict[str, Any]],
        params: dict[str, Any],
    ) -> None:
        if params.get("allow_nan"):
            assert any(m.get("is_nan") for m in metrics_list)

        if params.get("allow_infinity"):
            assert any(m.get("is_infinite") for m in metrics_list)

        for metrics in metrics_list:
            if metrics.get("is_nan") or metrics.get("is_infinite"):
                continue

            value = metrics["value"]
            if params["min_value"] is not None:
                assert value >= params["min_value"]
                if params["exclude_min"]:
                    assert value != params["min_value"]
            if params["max_value"] is not None:
                assert value <= params["max_value"]
                if params["exclude_max"]:
                    assert value != params["max_value"]


class TextConformance(ConformanceTest):
    """Conformance test for text string generation."""

    def params_strategy(self) -> st.SearchStrategy[dict[str, Any]]:
        @st.composite
        def strategy(draw: st.DrawFn) -> dict[str, Any]:
            use_min_size = draw(st.booleans())
            use_max_size = draw(st.booleans())

            min_size = draw(st.integers(0, 50)) if use_min_size else 0
            max_size = (
                draw(st.integers(min_value=min_size, max_value=100))
                if use_max_size
                else None
            )

            return {"min_size": min_size, "max_size": max_size}

        return strategy()

    def validate(
        self,
        metrics_list: list[dict[str, Any]],
        params: dict[str, Any],
    ) -> None:
        for metrics in metrics_list:
            length = metrics["length"]
            assert length >= params["min_size"]
            if params["max_size"] is not None:
                assert length <= params["max_size"]


class BinaryConformance(ConformanceTest):
    """Conformance test for binary data generation."""

    def params_strategy(self) -> st.SearchStrategy[dict[str, Any]]:
        @st.composite
        def strategy(draw: st.DrawFn) -> dict[str, Any]:
            use_min_size = draw(st.booleans())
            use_max_size = draw(st.booleans())

            min_size = draw(st.integers(0, 50)) if use_min_size else 0
            max_size = (
                draw(st.integers(min_value=min_size, max_value=100))
                if use_max_size
                else None
            )

            return {"min_size": min_size, "max_size": max_size}

        return strategy()

    def validate(
        self,
        metrics_list: list[dict[str, Any]],
        params: dict[str, Any],
    ) -> None:
        for metrics in metrics_list:
            length = metrics["length"]
            assert length >= params["min_size"]
            if params["max_size"] is not None:
                assert length <= params["max_size"]


class ListConformance(ConformanceTest):
    """Conformance test for list generation with element and size bounds."""

    def __init__(
        self,
        binary_path: str | Path,
        test_cases: int | None = None,
        *,
        min_value: int | None = None,
        max_value: int | None = None,
    ) -> None:
        super().__init__(binary_path, test_cases)
        self.min_value = min_value
        self.max_value = max_value

    def params_strategy(self) -> st.SearchStrategy[dict[str, Any]]:
        min_value = self.min_value
        max_value = self.max_value

        @st.composite
        def strategy(draw: st.DrawFn) -> dict[str, Any]:
            use_min_size = draw(st.booleans())
            use_max_size = draw(st.booleans())

            min_size = draw(st.integers(0, 100)) if use_min_size else 0
            max_size = (
                draw(st.integers(min_value=min_size, max_value=100))
                if use_max_size
                else None
            )

            return {
                "min_size": min_size,
                "max_size": max_size,
                **draw(_integer_params_strategy(min_value, max_value)),
            }

        return strategy()

    def validate(
        self,
        metrics_list: list[dict[str, Any]],
        params: dict[str, Any],
    ) -> None:
        for metrics in metrics_list:
            size = metrics["size"]
            assert size >= params["min_size"]
            if params["max_size"] is not None:
                assert size <= params["max_size"]

            if size > 0:
                if params["min_value"] is not None:
                    assert metrics["min_element"] >= params["min_value"]
                if params["max_value"] is not None:
                    assert metrics["max_element"] <= params["max_value"]


class SampledFromConformance(ConformanceTest):
    """Conformance test for sampled_from generation."""

    def params_strategy(self) -> st.SearchStrategy[dict[str, Any]]:
        @st.composite
        def strategy(draw: st.DrawFn) -> dict[str, Any]:
            options = draw(
                st.lists(
                    st.integers(-1000, 1000),
                    min_size=1,
                    max_size=10,
                    unique=True,
                ),
            )
            return {"options": options}

        return strategy()

    def validate(
        self,
        metrics_list: list[dict[str, Any]],
        params: dict[str, Any],
    ) -> None:
        for metrics in metrics_list:
            assert metrics["value"] in params["options"]


class DictConformance(ConformanceTest):
    """Conformance test for dictionary generation."""

    def __init__(
        self,
        binary_path: str | Path,
        test_cases: int | None = None,
        *,
        min_key: int | None = None,
        max_key: int | None = None,
        min_value: int | None = None,
        max_value: int | None = None,
    ) -> None:
        super().__init__(binary_path, test_cases)
        self.min_key = min_key if min_key is not None else -1000
        self.max_key = max_key if max_key is not None else 1000
        self.min_value = min_value if min_value is not None else -1000
        self.max_value = max_value if max_value is not None else 1000

    def params_strategy(self) -> st.SearchStrategy[dict[str, Any]]:
        min_key = self.min_key
        max_key = self.max_key
        min_value = self.min_value
        max_value = self.max_value

        @st.composite
        def strategy(draw: st.DrawFn) -> dict[str, Any]:
            min_size = draw(st.integers(0, 5))
            max_size = draw(st.integers(min_value=min_size, max_value=10))
            key_type = draw(st.sampled_from(["string", "integer"]))

            # For integer keys, ensure the key range is at least as large as max_size
            # to avoid "Cannot create collection with N unique elements from M distinct"
            # Constraint: drawn_min_key + max_size - 1 <= max_key
            max_allowed_min_key = max_key - max_size + 1
            drawn_min_key = draw(
                st.integers(
                    min_value=min_key,
                    max_value=max(min_key, max_allowed_min_key),
                ),
            )
            # Ensure at least max_size distinct keys are possible
            key_range_min = drawn_min_key + max_size - 1
            drawn_max_key = draw(
                st.integers(min_value=key_range_min, max_value=max_key),
            )

            # For values, draw bounds within the allowed range
            drawn_min_value = draw(
                st.integers(min_value=min_value, max_value=max_value),
            )
            drawn_max_value = draw(
                st.integers(min_value=drawn_min_value, max_value=max_value),
            )

            return {
                "min_size": min_size,
                "max_size": max_size,
                "key_type": key_type,
                "min_key": drawn_min_key,
                "max_key": drawn_max_key,
                "min_value": drawn_min_value,
                "max_value": drawn_max_value,
            }

        return strategy()

    def validate(
        self,
        metrics_list: list[dict[str, Any]],
        params: dict[str, Any],
    ) -> None:
        for metrics in metrics_list:
            size = metrics["size"]
            assert size >= params["min_size"]
            assert size <= params["max_size"]

            if size > 0:
                # Check value bounds
                assert metrics["min_value"] >= params["min_value"]
                assert metrics["max_value"] <= params["max_value"]

                # Check key bounds for integer keys
                if params["key_type"] == "integer":
                    assert metrics["min_key"] >= params["min_key"]
                    assert metrics["max_key"] <= params["max_key"]


def run_conformance_tests(
    tests: list[ConformanceTest],
    subtests: pytest.Subtests,
    *,
    settings: Settings | None = None,
) -> None:
    """Run all conformance tests using pytest subtests."""
    assert {type(t) for t in tests} == ConformanceTest.registered_tests

    for test in tests:
        with subtests.test(msg=type(test).__name__):

            def make_run_test(
                _test: ConformanceTest,
            ) -> Callable[[], None]:
                @Settings(parent=settings, max_examples=5, deadline=None)
                @given(_test.params_strategy())
                def run_test(params: dict[str, Any]) -> None:
                    _test.run(params)

                return run_test

            make_run_test(test)()
