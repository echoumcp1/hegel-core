"""Tests for sdk.py uncovered paths."""

import contextlib
import socket
import sys
import threading
import typing
from dataclasses import dataclass
from enum import Enum
from threading import Thread
from unittest.mock import MagicMock, patch

try:
    ExceptionGroup
except NameError:
    from exceptiongroup import ExceptionGroup  # type: ignore[no-redef]

import pytest

from hegel.hegeld import run_server_on_connection
from hegel.protocol import Connection, RequestError
from hegel.sdk import (
    Client,
    CompositeDictGenerator,
    CompositeListGenerator,
    CompositeOneOfGenerator,
    CompositeTupleGenerator,
    DataclassGenerator,
    FilteredGenerator,
    Generator,
    SampledFromGenerator,
    SchemaDictGenerator,
    _current_channel,
    _extract_origin,
    _find_hegeld,
    _get_channel,
    _HegelSession,
    _is_final,
    _test_aborted,
    assume,
    binary,
    collection,
    dicts,
    from_type,
    generate_from_schema as draw,
    integers,
    just,
    lists,
    note,
    one_of,
    optional,
    sampled_from,
    start_span,
    stop_span,
    text,
    tuples,
)


def _make_client():
    """Create a client/server pair for testing."""
    server_socket, client_socket = socket.socketpair()
    thread = Thread(
        target=run_server_on_connection,
        args=(Connection(server_socket, name="Server"),),
        daemon=True,
    )
    thread.start()
    client_connection = Connection(client_socket, name="Client")
    client = Client(client_connection)
    return client, client_connection, thread


# ---- note() function ----


def test_note_when_not_final(capsys):
    """Test note() is a no-op when not in final run."""
    client, conn, thread = _make_client()
    try:

        def my_test():
            note("should not print")
            x = draw({"type": "integer", "minimum": 0, "maximum": 10})
            assert isinstance(x, int)

        client.run_test("test_note", my_test, test_cases=5)
    finally:
        conn.close()
        thread.join(timeout=5)


# ---- assume() ----


def test_assume_true_passes():
    """Test assume(True) does not raise."""
    client, conn, thread = _make_client()
    try:

        def my_test():
            assume(condition=True)

        client.run_test("test_assume_true", my_test, test_cases=5)
    finally:
        conn.close()
        thread.join(timeout=5)


# ---- Generator combinators ----


def test_mapped_generator():
    """Test MappedGenerator."""
    client, conn, thread = _make_client()
    try:

        def my_test():
            gen = integers(min_value=0, max_value=10).map(lambda x: x * 2)
            assert gen.schema() is None
            v = gen.generate()
            assert v % 2 == 0

        client.run_test("test_map", my_test, test_cases=10)
    finally:
        conn.close()
        thread.join(timeout=5)


def test_flat_mapped_generator():
    """Test FlatMappedGenerator."""
    client, conn, thread = _make_client()
    try:

        def my_test():
            gen = integers(min_value=1, max_value=5).flat_map(
                lambda n: lists(integers(min_value=0, max_value=10), max_size=n),
            )
            assert gen.schema() is None
            v = gen.generate()
            assert isinstance(v, list)

        client.run_test("test_flatmap", my_test, test_cases=10)
    finally:
        conn.close()
        thread.join(timeout=5)


def test_filtered_generator():
    """Test FilteredGenerator."""
    client, conn, thread = _make_client()
    try:

        def my_test():
            gen = integers(min_value=0, max_value=100).filter(lambda x: x % 2 == 0)
            assert gen.schema() is None
            v = gen.generate()
            assert v % 2 == 0

        client.run_test("test_filter", my_test, test_cases=10)
    finally:
        conn.close()
        thread.join(timeout=5)


def test_filtered_generator_max_attempts_exhausted():
    """Test FilteredGenerator when all attempts fail calls assume(False)."""
    client, conn, thread = _make_client()
    try:

        def my_test():
            # Always-failing filter - will exhaust all 3 attempts
            gen = FilteredGenerator(
                integers(min_value=0, max_value=10),
                lambda x: False,
            )
            gen.generate()  # Should call assume(False) -> AssumeRejected

        # This test should pass (assume rejects all test cases)
        client.run_test("test_filter_exhaust", my_test, test_cases=10)
    finally:
        conn.close()
        thread.join(timeout=5)


# ---- Composite generators ----


def test_composite_list_generator():
    """Test CompositeListGenerator (elements without schema)."""
    client, conn, thread = _make_client()
    try:

        def my_test():
            # Create generator with no schema (mapped)
            elem = integers(min_value=0, max_value=10).map(lambda x: x * 2)
            gen = lists(elem, min_size=1, max_size=3)
            assert isinstance(gen, CompositeListGenerator)
            v = gen.generate()
            assert isinstance(v, list)
            assert 1 <= len(v) <= 3

        client.run_test("test_comp_list", my_test, test_cases=5)
    finally:
        conn.close()
        thread.join(timeout=5)


def test_composite_list_no_max():
    """Test CompositeListGenerator with no max_size."""
    client, conn, thread = _make_client()
    try:

        def my_test():
            elem = integers(min_value=0, max_value=10).map(lambda x: x)
            gen = lists(elem)
            assert isinstance(gen, CompositeListGenerator)
            v = gen.generate()
            assert isinstance(v, list)

        client.run_test("test_comp_list_nomax", my_test, test_cases=5)
    finally:
        conn.close()
        thread.join(timeout=5)


def test_composite_tuple_generator():
    """Test CompositeTupleGenerator (elements without schema)."""
    client, conn, thread = _make_client()
    try:

        def my_test():
            elem = integers(min_value=0, max_value=10).map(lambda x: x)
            gen = tuples(elem, integers())
            assert isinstance(gen, CompositeTupleGenerator)
            v = gen.generate()
            assert isinstance(v, tuple)
            assert len(v) == 2

        client.run_test("test_comp_tuple", my_test, test_cases=5)
    finally:
        conn.close()
        thread.join(timeout=5)


def test_composite_one_of_generator():
    """Test CompositeOneOfGenerator (generators without schema)."""
    client, conn, thread = _make_client()
    try:

        def my_test():
            gen = one_of(
                integers(min_value=0, max_value=10).map(lambda x: x),
                text(),
            )
            assert isinstance(gen, CompositeOneOfGenerator)
            v = gen.generate()
            assert isinstance(v, int | str)

        client.run_test("test_comp_oneof", my_test, test_cases=10)
    finally:
        conn.close()
        thread.join(timeout=5)


def test_composite_dict_generator():
    """Test CompositeDictGenerator (keys/values without schema)."""
    client, conn, thread = _make_client()
    try:

        def my_test():
            key_gen = text(min_size=1, max_size=3).map(lambda x: x)
            gen = dicts(key_gen, integers(), min_size=0, max_size=2)
            assert isinstance(gen, CompositeDictGenerator)
            v = gen.generate()
            assert isinstance(v, dict)

        client.run_test("test_comp_dict", my_test, test_cases=5)
    finally:
        conn.close()
        thread.join(timeout=5)


def test_composite_dict_no_max():
    """Test CompositeDictGenerator with no max_size."""
    client, conn, thread = _make_client()
    try:

        def my_test():
            key_gen = text(min_size=1).map(lambda x: x)
            gen = dicts(key_gen, integers())
            assert isinstance(gen, CompositeDictGenerator)
            v = gen.generate()
            assert isinstance(v, dict)

        client.run_test("test_comp_dict_nomax", my_test, test_cases=5)
    finally:
        conn.close()
        thread.join(timeout=5)


def test_schema_dict_generator():
    """Test SchemaDictGenerator."""
    client, conn, thread = _make_client()
    try:

        def my_test():
            gen = dicts(text(min_size=1), integers(), min_size=0, max_size=2)
            assert isinstance(gen, SchemaDictGenerator)
            v = gen.generate()
            assert isinstance(v, dict)

        client.run_test("test_schema_dict", my_test, test_cases=5)
    finally:
        conn.close()
        thread.join(timeout=5)


# ---- SampledFromGenerator ----


def test_sampled_from_non_primitive():
    """Test SampledFromGenerator with non-primitive (index-based)."""
    client, conn, thread = _make_client()
    try:

        class Custom:
            def __init__(self, x):
                self.x = x

        items = [Custom(1), Custom(2), Custom(3)]

        def my_test():
            gen = sampled_from(items)
            assert gen.schema() is None
            v = gen.generate()
            assert v in items

        client.run_test("test_sampled_nonprim", my_test, test_cases=10)
    finally:
        conn.close()
        thread.join(timeout=5)


# ---- binary() generator ----


def test_binary_generator():
    """Test binary() generator."""
    client, conn, thread = _make_client()
    try:

        def my_test():
            gen = binary(min_size=1, max_size=10)
            v = gen.generate()
            assert isinstance(v, str)  # base64 encoded

        client.run_test("test_binary_gen", my_test, test_cases=5)
    finally:
        conn.close()
        thread.join(timeout=5)


def test_binary_generator_no_max():
    """Test binary() generator without max_size."""
    client, conn, thread = _make_client()
    try:

        def my_test():
            gen = binary()
            v = gen.generate()
            assert isinstance(v, str)

        client.run_test("test_binary_nomax", my_test, test_cases=5)
    finally:
        conn.close()
        thread.join(timeout=5)


# ---- optional() ----


def test_optional_generator():
    """Test optional() generator."""
    client, conn, thread = _make_client()
    try:

        def my_test():
            gen = optional(integers(min_value=0, max_value=10))
            v = gen.generate()
            assert v is None or isinstance(v, int)

        client.run_test("test_optional", my_test, test_cases=20)
    finally:
        conn.close()
        thread.join(timeout=5)


# ---- just() ----


def test_just_generator():
    """Test just() generator."""
    client, conn, thread = _make_client()
    try:

        def my_test():
            gen = just(42)
            v = gen.generate()
            assert v == 42

        client.run_test("test_just", my_test, test_cases=5)
    finally:
        conn.close()
        thread.join(timeout=5)


# ---- DataclassGenerator ----


def test_dataclass_generator():
    """Test DataclassGenerator."""

    @dataclass
    class Point:
        x: int
        y: int

    client, conn, thread = _make_client()
    try:

        def my_test():
            gen = from_type(Point)
            assert isinstance(gen, DataclassGenerator)
            v = gen.generate()
            assert isinstance(v, Point)

        client.run_test("test_dataclass", my_test, test_cases=5)
    finally:
        conn.close()
        thread.join(timeout=5)


def test_dataclass_generator_with_field():
    """Test DataclassGenerator.with_field()."""

    @dataclass
    class Point:
        x: int
        y: int

    gen = DataclassGenerator(Point)
    new_gen = gen.with_field("x", integers(min_value=0, max_value=5))
    assert isinstance(new_gen, DataclassGenerator)

    with pytest.raises(ValueError, match="Unknown field"):
        gen.with_field("z", integers())


def test_dataclass_generator_compositional():
    """Test DataclassGenerator with fields that lack schema."""

    @dataclass
    class Thing:
        x: int
        label: str

    client, conn, thread = _make_client()
    try:

        def my_test():
            gen = DataclassGenerator(Thing)
            # Override a field with a mapped generator (no schema)
            gen = gen.with_field("label", text().map(lambda s: s.upper()))
            assert gen.schema() is None
            v = gen.generate()
            assert isinstance(v, Thing)

        client.run_test("test_dc_comp", my_test, test_cases=5)
    finally:
        conn.close()
        thread.join(timeout=5)


def test_dataclass_not_a_dataclass():
    """Test DataclassGenerator rejects non-dataclass."""
    with pytest.raises(TypeError, match="is not a dataclass"):
        DataclassGenerator(int)


# ---- from_type() paths ----


def test_from_type_none():
    """Test from_type(type(None))."""
    gen = from_type(type(None))
    assert gen.schema() is not None


def test_from_type_bytes():
    """Test from_type(bytes)."""
    gen = from_type(bytes)
    assert gen.schema() is not None


def test_from_type_float():
    """Test from_type(float)."""
    gen = from_type(float)
    assert gen.schema() is not None


def test_from_type_union():
    """Test from_type with Union types."""
    gen = from_type(int | str)
    assert gen is not None


def test_from_type_pipe_union():
    """Test from_type with T | None (Optional)."""
    gen = from_type(int | None)
    assert gen is not None


def test_from_type_list():
    """Test from_type(list[int])."""
    gen = from_type(list[int])
    assert gen is not None


def test_from_type_list_bare():
    """Test from_type(list) without args raises TypeError."""
    with pytest.raises(TypeError, match="Cannot generate"):
        from_type(list)


def test_from_type_dict():
    """Test from_type(dict[str, int])."""
    gen = from_type(dict[str, int])
    assert gen is not None


def test_from_type_dict_bare():
    """Test from_type(dict) without args raises TypeError."""
    with pytest.raises(TypeError, match="Cannot generate"):
        from_type(dict)


def test_from_type_tuple():
    """Test from_type(tuple[int, str])."""
    gen = from_type(tuple[int, str])
    assert gen is not None


def test_from_type_tuple_bare():
    """Test from_type(tuple) without args raises TypeError."""
    with pytest.raises(TypeError, match="Cannot generate"):
        from_type(tuple)


def test_from_type_set():
    """Test from_type(set[int])."""
    gen = from_type(set[int])
    assert gen is not None


def test_from_type_set_bare():
    """Test from_type(set) without args raises TypeError."""
    with pytest.raises(TypeError, match="Cannot generate"):
        from_type(set)


def test_from_type_enum():
    """Test from_type with Enum."""

    class Color(Enum):
        RED = 1
        GREEN = 2
        BLUE = 3

    gen = from_type(Color)
    assert gen is not None


def test_from_type_unsupported():
    """Test from_type with unsupported type."""
    with pytest.raises(TypeError, match="Cannot generate"):
        from_type(complex)


# ---- _extract_origin ----


def test_extract_origin_with_traceback():
    """Test _extract_origin with a real traceback."""
    try:
        raise ValueError("test")
    except ValueError as e:
        origin = _extract_origin(e, e.__traceback__)
        assert "ValueError" in origin
        assert "test_sdk_coverage.py" in origin


def test_extract_origin_no_traceback():
    """Test _extract_origin with None traceback."""
    origin = _extract_origin(ValueError("test"), None)
    assert "ValueError" in origin
    assert ":0" in origin


# ---- _find_hegeld ----


def test_find_hegeld_in_venv(tmp_path):
    """Test _find_hegeld finds binary in virtual environment."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    hegel_bin = bin_dir / "hegel"
    hegel_bin.touch()

    with (
        patch("hegel.sdk.sys") as mock_sys,
        patch("hegel.sdk.os.path.exists", return_value=True),
    ):
        mock_sys.prefix = str(tmp_path)
        mock_sys.base_prefix = "/different"
        result = _find_hegeld()
        assert "hegel" in result


def test_find_hegeld_in_path():
    """Test _find_hegeld finds hegel on PATH."""
    with (
        patch("hegel.sdk.sys") as mock_sys,
        patch("hegel.sdk.shutil.which", return_value="/usr/bin/hegel"),
    ):
        mock_sys.prefix = mock_sys.base_prefix = "/same"
        result = _find_hegeld()
        assert result == "/usr/bin/hegel"


def test_find_hegeld_fallback():
    """Test _find_hegeld falls back to python -m hegel."""
    with (
        patch("hegel.sdk.sys") as mock_sys,
        patch("hegel.sdk.shutil.which", return_value=None),
    ):
        mock_sys.prefix = mock_sys.base_prefix = "/same"
        mock_sys.executable = "/usr/bin/python3"
        result = _find_hegeld()
        assert result == "/usr/bin/python3 -m hegel"


# ---- _HegelSession ----


def test_hegel_session_cleanup():
    """Test _HegelSession._cleanup handles all branches."""
    session = _HegelSession()

    # Cleanup with nothing set should be a no-op
    session._cleanup()

    # Set up mock fields
    session._connection = MagicMock()
    session._client = MagicMock()
    session._process = MagicMock()
    session._sock = MagicMock()
    session._temp_dir = MagicMock()

    session._cleanup()

    assert session._connection is None
    assert session._client is None
    assert session._process is None
    assert session._sock is None
    assert session._temp_dir is None


def test_hegel_session_cleanup_with_exceptions():
    """Test _HegelSession._cleanup suppresses exceptions."""
    session = _HegelSession()

    session._connection = MagicMock()
    session._connection.close.side_effect = OSError("fail")
    session._process = MagicMock()
    session._process.terminate.side_effect = OSError("fail")
    session._sock = MagicMock()
    session._sock.close.side_effect = OSError("fail")
    session._temp_dir = MagicMock()
    session._temp_dir.cleanup.side_effect = OSError("fail")

    session._cleanup()

    assert session._connection is None
    assert session._process is None
    assert session._sock is None
    assert session._temp_dir is None


def test_hegel_session_start_and_run():
    """Test _HegelSession full lifecycle."""
    session = _HegelSession()
    try:
        session._start()
        assert session._client is not None
        assert session._connection is not None

        # Calling _start again should be a no-op
        session._start()
    finally:
        session._cleanup()


def test_hegel_session_run_test():
    """Test _HegelSession.run_test."""
    session = _HegelSession()
    try:

        def my_test():
            x = draw({"type": "integer", "minimum": 0, "maximum": 10})
            assert isinstance(x, int)

        session.run_test(my_test, test_cases=5)
    finally:
        session._cleanup()


# ---- start_span/stop_span when aborted ----


def test_start_stop_span_when_aborted():
    """Test start_span/stop_span are no-ops when test is aborted."""
    token_aborted = _test_aborted.set(True)
    token_channel = _current_channel.set(MagicMock())
    try:
        # Should be no-ops (not raise)
        start_span(1)
        stop_span()
    finally:
        _test_aborted.reset(token_aborted)
        _current_channel.reset(token_channel)


# ---- Composite generators through server ----


def test_composite_list_generator_through_server():
    """Test CompositeListGenerator with live server."""
    client, client_conn, thread = _make_client()
    try:

        def my_test():
            # MappedGenerator has no schema, so lists() uses CompositeListGenerator
            mapped = integers().map(lambda x: x * 2)
            result = lists(mapped, min_size=1, max_size=3).generate()
            assert isinstance(result, list)
            assert 1 <= len(result) <= 3

        client.run_test("test_composite_list", my_test, test_cases=5)
    finally:
        client_conn.close()
        thread.join(timeout=5)


def test_composite_tuple_generator_through_server():
    """Test CompositeTupleGenerator with live server."""
    client, client_conn, thread = _make_client()
    try:

        def my_test():
            # MappedGenerator has no schema, so tuples() uses CompositeTupleGenerator
            mapped = integers().map(lambda x: x * 2)
            result = tuples(mapped, integers()).generate()
            assert isinstance(result, tuple)
            assert len(result) == 2

        client.run_test("test_composite_tuple", my_test, test_cases=5)
    finally:
        client_conn.close()
        thread.join(timeout=5)


def test_composite_one_of_generator_through_server():
    """Test CompositeOneOfGenerator with live server."""
    client, client_conn, thread = _make_client()
    try:

        def my_test():
            mapped = integers().map(lambda x: x * 2)
            result = one_of(mapped, text()).generate()
            assert isinstance(result, int | str)

        client.run_test("test_composite_one_of", my_test, test_cases=5)
    finally:
        client_conn.close()
        thread.join(timeout=5)


def test_composite_dict_generator_through_server():
    """Test CompositeDictGenerator with live server."""
    client, client_conn, thread = _make_client()
    try:

        def my_test():
            mapped_keys = text().map(lambda x: x.upper())
            result = dicts(mapped_keys, integers(), min_size=0, max_size=3).generate()
            assert isinstance(result, dict)

        client.run_test("test_composite_dict", my_test, test_cases=5)
    finally:
        client_conn.close()
        thread.join(timeout=5)


def test_sampled_from_non_primitive_through_server():
    """Test SampledFromGenerator fallback mode with non-primitives."""
    client, client_conn, thread = _make_client()
    try:
        obj_a = object()
        obj_b = object()

        def my_test():
            result = sampled_from([obj_a, obj_b]).generate()
            assert result is obj_a or result is obj_b

        client.run_test("test_sampled_non_primitive", my_test, test_cases=5)
    finally:
        client_conn.close()
        thread.join(timeout=5)


def test_schema_dict_generator_through_server():
    """Test SchemaDictGenerator with live server."""
    client, client_conn, thread = _make_client()
    try:

        def my_test():
            result = dicts(text(), integers(), min_size=0, max_size=2).generate()
            assert isinstance(result, dict)

        client.run_test("test_schema_dict", my_test, test_cases=5)
    finally:
        client_conn.close()
        thread.join(timeout=5)


def test_dataclass_with_composite_field_through_server():
    """Test DataclassGenerator with fields that have no schema."""

    @dataclass
    class Point:
        x: int
        y: int

    client, client_conn, thread = _make_client()
    try:

        def my_test():
            gen = DataclassGenerator(Point)
            mapped_gen = integers().map(lambda x: x * 2)
            gen_with_override = gen.with_field("x", mapped_gen)
            # This forces compositional fallback in DataclassGenerator.generate()
            result = gen_with_override.generate()
            assert isinstance(result, Point)

        client.run_test("test_dataclass_composite", my_test, test_cases=5)
    finally:
        client_conn.close()
        thread.join(timeout=5)


def test_note_on_final_run():
    """Test note() prints on final run."""
    token_final = _is_final.set(True)
    token_channel = _current_channel.set(MagicMock())
    try:
        # Should print to stderr
        note("test message")
    finally:
        _is_final.reset(token_final)
        _current_channel.reset(token_channel)


def test_get_channel_outside_context():
    """Test _get_channel raises RuntimeError outside test context."""
    with pytest.raises(RuntimeError, match="Not in a test context"):
        _get_channel()


def test_generator_base_schema():
    """Test Generator.schema() base class returns None."""

    class MyGen(integers().__class__.__bases__[0]):
        def generate(self):
            return 42

    # Use Generator base directly
    class SimpleGen(Generator):
        def generate(self):
            return 42

    assert SimpleGen().schema() is None


def test_mapped_generator_schema():
    """Test MappedGenerator.schema() returns None."""
    gen = integers().map(lambda x: x * 2)
    assert gen.schema() is None


def test_flat_mapped_generator_schema():
    """Test FlatMappedGenerator.schema() returns None."""
    gen = integers().flat_map(lambda x: integers())
    assert gen.schema() is None


def test_filtered_generator_schema():
    """Test FilteredGenerator.schema() returns None."""
    gen = integers().filter(lambda x: x > 0)
    assert gen.schema() is None


def test_schema_dict_generator_schema():
    """Test SchemaDictGenerator.schema() returns schema."""
    gen = SchemaDictGenerator(
        {
            "type": "dict",
            "keys": {"type": "string"},
            "values": {"type": "integer"},
        },
    )
    assert gen.schema() is not None
    assert gen.schema()["type"] == "dict"


def test_dataclass_generator_schema_with_all_schemas():
    """Test DataclassGenerator.schema() when all fields have schemas."""

    @dataclass
    class Point:
        x: int
        y: int

    gen = DataclassGenerator(Point)
    schema = gen.schema()
    assert schema is not None
    assert schema["type"] == "object"
    assert "x" in schema["properties"]
    assert "y" in schema["properties"]


def test_dataclass_generator_schema_returns_none_for_non_schema_field():
    """Test DataclassGenerator.schema() returns None when field has no schema."""

    @dataclass
    class Point:
        x: int
        y: int

    gen = DataclassGenerator(Point)
    # Override with a generator that has no schema
    gen_override = gen.with_field("x", integers().map(lambda x: x))
    assert gen_override.schema() is None


def test_from_type_optional():
    """Test from_type with Optional[int]."""
    gen = from_type(int | None)
    assert gen is not None


def test_from_type_union_typing():
    """Test from_type with Union[int, str] via typing module."""
    gen = from_type(int | str)
    assert gen is not None


def test_from_type_union_type():
    """Test from_type with int | str (UnionType)."""
    gen = from_type(int | str)
    assert gen is not None


def test_from_type_typing_list_subscripted():
    """Test from_type with typing.List[int]."""

    gen = from_type(list[int])
    assert gen is not None


def test_from_type_typing_list_bare():
    """Test from_type with typing.List (no args) - hits default branch."""
    gen = from_type(typing.List)  # noqa: UP006
    assert gen is not None


def test_from_type_typing_dict_subscripted():
    """Test from_type with typing.Dict[str, int]."""

    gen = from_type(dict[str, int])
    assert gen is not None


def test_from_type_typing_dict_bare():
    """Test from_type with typing.Dict (no args) - hits default branch."""
    gen = from_type(typing.Dict)  # noqa: UP006
    assert gen is not None


def test_from_type_typing_tuple_subscripted():
    """Test from_type with typing.Tuple[int, str]."""

    gen = from_type(tuple[int, str])
    assert gen is not None


def test_from_type_typing_tuple_bare():
    """Test from_type with typing.Tuple (no args) - hits default branch."""
    gen = from_type(typing.Tuple)  # noqa: UP006
    assert gen is not None


def test_from_type_typing_set_subscripted():
    """Test from_type with typing.Set[int]."""

    gen = from_type(set[int])
    assert gen is not None


def test_from_type_typing_set_bare():
    """Test from_type with typing.Set (no args) - hits default branch."""
    gen = from_type(typing.Set)  # noqa: UP006
    assert gen is not None


def test_failing_test_single_interesting():
    """Test that a single failing test raises properly."""
    client, client_conn, thread = _make_client()
    try:

        def my_test():
            x = draw({"type": "integer", "minimum": 0, "maximum": 100})
            assert x < 50

        with pytest.raises(AssertionError):
            client.run_test("test_fail", my_test, test_cases=100)
    finally:
        client_conn.close()
        thread.join(timeout=5)


def test_binary_generator_schema():
    """Test binary() generator factory function."""
    gen = binary()
    assert gen.schema() is not None
    assert gen.schema()["type"] == "binary"

    gen_with_max = binary(min_size=5, max_size=10)
    schema = gen_with_max.schema()
    assert schema["min_size"] == 5
    assert schema["max_size"] == 10


def test_sampled_from_schema_type_error():
    """Test SampledFromGenerator.schema() catches TypeError/ValueError."""

    class BadObj:
        def __eq__(self, other):
            raise TypeError("nope")

    gen = SampledFromGenerator([BadObj()])
    assert gen.schema() is None


def test_from_type_union_type_with_args():
    """Test from_type with int | str type."""
    gen = from_type(int | str | float)
    assert gen is not None


def test_find_hegeld_in_venv_not_exists():
    """Test _find_hegeld when venv hegel doesn't exist."""
    with (
        patch("hegel.sdk.sys") as mock_sys,
        patch("hegel.sdk.os.path.exists", return_value=False),
        patch("hegel.sdk.shutil.which", return_value="/usr/local/bin/hegel"),
    ):
        mock_sys.prefix = "/some/venv"
        mock_sys.base_prefix = "/different"
        result = _find_hegeld()
        assert result == "/usr/local/bin/hegel"


def test_multiple_interesting_exception_group():
    """Test ExceptionGroup is raised when there are multiple interesting examples.

    This covers sdk.py lines 180-181 (ExceptionGroup path) which requires
    n_interesting > 1 (multiple failure origins).
    """
    client, conn, thread = _make_client()
    try:

        def my_test():
            x = draw({"type": "integer", "minimum": 0, "maximum": 1000})
            # Two different assertion locations = two different origins
            if x < 500:
                assert x < 0, "first origin"
            else:
                assert x < 500, "second origin"

        with pytest.raises(ExceptionGroup):
            client.run_test("test_multi", my_test, test_cases=200)
    finally:
        conn.close()
        thread.join(timeout=10)


def test_generate_from_schema_non_stop_test_error():
    """Test generate_from_schema re-raises non-StopTest RequestError.

    This covers sdk.py line 267.
    """
    client, conn, thread = _make_client()
    try:

        def my_test():
            # Invalid schema type should cause a server-side error
            with pytest.raises(RequestError):
                draw({"type": "completely_invalid_schema_type"})

        client.run_test("test_bad_schema", my_test, test_cases=1)
    finally:
        conn.close()
        thread.join(timeout=5)


def test_connection_error_in_test_function():
    """Test ConnectionError is re-raised from test function.

    This covers sdk.py line 205.
    """
    client, conn, thread = _make_client()
    try:

        def my_test():
            raise ConnectionError("test connection lost")

        with pytest.raises(ConnectionError, match="test connection lost"):
            client.run_test("test_conn_err", my_test, test_cases=1)
    finally:
        conn.close()
        thread.join(timeout=5)


def test_unrecognised_event_in_run_test():
    """Test unrecognised event handling in Client.run_test.

    This covers sdk.py line 141 by injecting a bad event through
    the protocol directly instead of going through the normal server.
    """

    server_socket, client_socket = socket.socketpair()
    server_conn = Connection(server_socket, name="Server")
    client_conn = Connection(client_socket, name="Client")

    def fake_server():
        server_conn.receive_handshake()
        control = server_conn.control_channel

        # Receive run_test command
        msg_id, message = control.receive_request()
        test_channel = server_conn.connect_channel(
            message["channel"],
            role="Test",
        )
        control.send_response_value(msg_id, message=True)

        # Send an unrecognised event
        req_id = test_channel.send_request({"event": "bogus_event"})
        # Receive the error response
        test_channel.receive_response_raw(req_id)

        # Now send test_done
        test_channel.request(
            {
                "event": "test_done",
                "results": {
                    "passed": True,
                    "examples_run": 0,
                    "valid_test_cases": 0,
                    "invalid_test_cases": 0,
                    "interesting_test_cases": 0,
                },
            },
        ).get()

    t = Thread(target=fake_server, daemon=True)
    t.start()

    try:
        client = Client(client_conn)
        client.run_test("test_bogus", lambda: None, test_cases=1)
    finally:
        client_conn.close()
        server_conn.close()
        t.join(timeout=5)


def test_hegel_session_start_verbose_double_check_lock():
    """Test _HegelSession._start double-check lock path.

    This covers sdk.py line 953 by calling _start twice rapidly.
    """
    session = _HegelSession()
    started = threading.Event()
    errors = []

    def start_session():
        try:
            session._start()
            started.set()
        except Exception as e:
            errors.append(e)

    try:
        # Start from two threads simultaneously
        t1 = Thread(target=start_session)
        t2 = Thread(target=start_session)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)
        assert not errors
        assert session._client is not None
    finally:
        session._cleanup()


def test_hegel_session_timeout_kill():
    """Test _HegelSession._start kills process on timeout.

    This covers sdk.py lines 990-991.
    """
    session = _HegelSession()

    # Mock _find_hegeld to return a command that creates a socket file
    # but never listens on it (so connect fails every time)
    with (
        patch("hegel.sdk._find_hegeld", return_value=sys.executable),
        patch("hegel.sdk.subprocess.Popen") as mock_popen,
        patch("hegel.sdk.os.path.exists", return_value=False),
        patch("hegel.sdk.time.sleep"),
    ):
        mock_process = MagicMock()
        mock_popen.return_value = mock_process

        with pytest.raises(RuntimeError, match="Timeout"):
            session._start()

        mock_process.kill.assert_called_once()


def test_hegel_session_connection_retry():
    """Test _HegelSession._start retries connection.

    This covers sdk.py lines 984-986.
    """
    session = _HegelSession()

    with (
        patch("hegel.sdk._find_hegeld", return_value=sys.executable),
        patch("hegel.sdk.subprocess.Popen") as mock_popen,
        patch("hegel.sdk.os.path.exists", return_value=True),
        patch("hegel.sdk.socket.socket") as mock_socket_cls,
        patch("hegel.sdk.time.sleep"),
    ):
        mock_process = MagicMock()
        mock_popen.return_value = mock_process
        mock_sock = MagicMock()
        mock_sock.connect.side_effect = ConnectionRefusedError("not ready")
        mock_socket_cls.return_value = mock_sock

        with pytest.raises(RuntimeError, match="Timeout"):
            session._start()

        # Verify close was called on failed connection attempts
        assert mock_sock.close.call_count > 0


def test_sampled_from_schema_iteration_error():
    """Test SampledFromGenerator.schema() catches TypeError during iteration.

    This covers sdk.py lines 612-613 (except TypeError/ValueError).
    """

    class BadList(list):
        """List that raises TypeError when iterated."""

        def __iter__(self):
            raise TypeError("iteration error")

    gen = SampledFromGenerator.__new__(SampledFromGenerator)
    gen._elements = BadList([1, 2, 3])
    gen._json_values = None
    gen._schema_computed = False
    gen._cached_schema = None
    assert gen.schema() is None


def test_sampled_from_generate_server_wrong_value():
    """Test SampledFromGenerator.generate() raises when server returns wrong value.

    This covers sdk.py line 625.
    """

    gen = SampledFromGenerator([1, 2, 3])
    # Force schema to return a valid schema
    assert gen.schema() is not None

    # Mock generate_from_schema to return a value NOT in the elements
    with (
        patch("hegel.sdk.generate_from_schema", return_value=999),
        pytest.raises(RuntimeError, match="Server returned 999"),
    ):
        gen.generate()


def test_is_final_pass_with_multiple_interesting():
    """Test the branch where is_final test case doesn't fail with n_interesting > 1.

    This covers sdk.py line 171 (the AssertionError for is_final pass).
    """
    client, conn, thread = _make_client()
    try:

        def my_test():
            x = draw({"type": "integer", "minimum": 0, "maximum": 1000})
            # Fail in different places to get multiple interesting examples
            if x < 500:
                assert x < 0
            else:
                assert x < 500

        # Patch _run_test_case so that during is_final, it doesn't raise
        original_run_test_case = Client._run_test_case

        is_final_count = [0]

        def patched_run_test_case(self, channel, test_fn, *, is_final):
            if is_final:
                is_final_count[0] += 1
                # Send mark_complete but suppress the RequestError that comes
                # from the server raising StopTest
                with contextlib.suppress(RequestError):
                    channel.request(
                        {"command": "mark_complete", "status": "VALID"},
                    ).get()
                return
            return original_run_test_case(self, channel, test_fn, is_final=is_final)

        with (
            patch.object(Client, "_run_test_case", patched_run_test_case),
            pytest.raises(ExceptionGroup),
        ):
            client.run_test("test_final_pass", my_test, test_cases=200)

        # Verify is_final was called multiple times
        assert is_final_count[0] > 1
    finally:
        conn.close()
        thread.join(timeout=10)


def test_nested_test_case_raises():
    """Test that nesting test cases raises RuntimeError.

    Covers sdk.py line 190.
    """
    client, conn, thread = _make_client()
    try:

        def my_test():
            channel = _get_channel()
            # _current_channel is already set, calling _run_test_case again
            # should raise RuntimeError
            with pytest.raises(RuntimeError, match="Cannot nest test cases"):
                client._run_test_case(channel, lambda: None, is_final=False)

        client.run_test("test_nested", my_test, test_cases=1)
    finally:
        conn.close()
        thread.join(timeout=5)


def test_collection_more_after_finished():
    """Test collection.more() returns False when already finished.

    Covers sdk.py line 536.
    """
    client, conn, thread = _make_client()
    try:

        def my_test():
            c = collection("test_coll", min_size=0, max_size=1)
            # Drain the collection
            while c.more():
                draw({"type": "integer"})
            # Now call more() again — should return False immediately
            assert c.more() is False

        client.run_test("test_more_finished", my_test, test_cases=5)
    finally:
        conn.close()
        thread.join(timeout=5)


def test_collection_reject_while_active():
    """Test collection.reject() while collection is active.

    Covers sdk.py line 550 (the body of reject when not finished).
    """
    client, conn, thread = _make_client()
    try:

        def my_test():
            c = collection("test_coll", min_size=1, max_size=5)
            while c.more():
                val = draw({"type": "integer", "minimum": 0, "maximum": 100})
                if val % 2 != 0:
                    c.reject()

        client.run_test("test_reject_active", my_test, test_cases=10)
    finally:
        conn.close()
        thread.join(timeout=5)


def test_collection_reject_when_finished():
    """Test collection.reject() is a no-op when collection is finished.

    Covers sdk.py lines 549 (the False branch of if not self.__finished).
    """
    client, conn, thread = _make_client()
    try:

        def my_test():
            c = collection("test_coll", min_size=0, max_size=1)
            # Drain the collection
            while c.more():
                draw({"type": "integer"})
            # Now call reject() — should be a no-op (returns None)
            result = c.reject()
            assert result is None

        client.run_test("test_reject_finished", my_test, test_cases=5)
    finally:
        conn.close()
        thread.join(timeout=5)
