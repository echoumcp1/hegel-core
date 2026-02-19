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

from hegel.protocol import Connection, RequestError
from hegel_sdk import (
    BasicGenerator,
    Client,
    CompositeDictGenerator,
    CompositeListGenerator,
    CompositeOneOfGenerator,
    CompositeTupleGenerator,
    DataclassGenerator,
    FilteredGenerator,
    MappedGenerator,
    assume,
    binary,
    booleans,
    collection,
    dicts,
    from_type,
    generate_from_schema,
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
from hegel_sdk.client import (
    _current_channel,
    _extract_origin,
    _get_channel,
    _is_final,
    _test_aborted,
)
from hegel_sdk.session import _find_hegeld, _HegelSession

# ---- note() function ----


def test_note_when_not_final(client, capsys):
    """Test note() is a no-op when not in final run."""

    def test():
        note("should not print")
        x = generate_from_schema({"type": "integer", "min_value": 0, "max_value": 10})
        assert isinstance(x, int)

    client.run_test("test_note", test, test_cases=5)


def test_note_on_final_run():
    """Test note() prints on final run."""
    token_final = _is_final.set(True)
    token_channel = _current_channel.set(MagicMock())
    try:
        note("test message")
    finally:
        _is_final.reset(token_final)
        _current_channel.reset(token_channel)


# ---- assume() ----


def test_assume_true_passes(client):
    """Test assume(True) does not raise."""

    def test():
        assume(condition=True)

    client.run_test("test_assume_true", test, test_cases=5)


# ---- Generator combinators ----


def test_mapped_generator(client):
    """Test mapping on BasicGenerator preserves schema."""

    def test():
        gen = integers(min_value=0, max_value=10).map(lambda x: x * 2)
        assert isinstance(gen, BasicGenerator)
        v = gen.generate()
        assert v % 2 == 0

    client.run_test("test_map", test, test_cases=10)


def test_flat_mapped_generator(client):
    """Test FlatMappedGenerator."""

    def test():
        gen = integers(min_value=1, max_value=5).flat_map(
            lambda n: lists(integers(min_value=0, max_value=10), max_size=n),
        )
        assert not isinstance(gen, BasicGenerator)
        v = gen.generate()
        assert isinstance(v, list)

    client.run_test("test_flatmap", test, test_cases=10)


def test_filtered_generator(client):
    """Test FilteredGenerator."""

    def test():
        gen = integers(min_value=0, max_value=100).filter(lambda x: x % 2 == 0)
        assert not isinstance(gen, BasicGenerator)
        v = gen.generate()
        assert v % 2 == 0

    client.run_test("test_filter", test, test_cases=10)


def test_filtered_generator_max_attempts_exhausted(client):
    """Test FilteredGenerator when all attempts fail calls assume(False)."""

    def test():
        gen = FilteredGenerator(
            integers(min_value=0, max_value=10),
            lambda x: False,
        )
        gen.generate()

    client.run_test("test_filter_exhaust", test, test_cases=10)


def test_basic_generator_double_map(client):
    """Test BasicGenerator.map() when already has a transform (compose transforms)."""

    def test():
        gen = (
            integers(min_value=1, max_value=5).map(lambda x: x * 2).map(lambda x: x + 1)
        )
        assert isinstance(gen, BasicGenerator)
        assert gen.schema()["type"] == "integer"
        v = gen.generate()
        # 1*2+1=3, 2*2+1=5, 3*2+1=7, 4*2+1=9, 5*2+1=11
        assert v in [3, 5, 7, 9, 11]

    client.run_test("test_double_map", test, test_cases=10)


def test_mapped_generator_schema():
    """Test BasicGenerator.map() preserves schema."""
    gen = integers().map(lambda x: x * 2)
    assert isinstance(gen, BasicGenerator)
    assert gen.schema() is not None


def test_mapped_generator_on_non_basic():
    """Test map() on non-BasicGenerator creates MappedGenerator."""
    filtered = integers().filter(lambda x: x > 0)
    mapped = filtered.map(lambda x: x * 2)
    assert isinstance(mapped, MappedGenerator)
    assert not isinstance(mapped, BasicGenerator)


def test_flat_mapped_generator_not_basic():
    assert not isinstance(integers().flat_map(lambda x: integers()), BasicGenerator)


def test_filtered_generator_not_basic():
    assert not isinstance(integers().filter(lambda x: x > 0), BasicGenerator)


# ---- Composite generators ----


def test_composite_list_generator(client):
    """Test CompositeListGenerator (elements without BasicGenerator schema)."""

    def test():
        elem = integers(min_value=0, max_value=10).filter(lambda x: x % 2 == 0)
        gen = lists(elem, min_size=1, max_size=3)
        assert isinstance(gen, CompositeListGenerator)
        v = gen.generate()
        assert isinstance(v, list)
        assert 1 <= len(v) <= 3

    client.run_test("test_comp_list", test, test_cases=5)


def test_composite_list_no_max(client):
    """Test CompositeListGenerator with no max_size."""

    def test():
        elem = integers(min_value=0, max_value=10).filter(lambda x: True)
        gen = lists(elem)
        assert isinstance(gen, CompositeListGenerator)
        v = gen.generate()
        assert isinstance(v, list)

    client.run_test("test_comp_list_nomax", test, test_cases=5)


def test_composite_tuple_generator(client):
    """Test CompositeTupleGenerator (elements without BasicGenerator schema)."""

    def test():
        elem = integers(min_value=0, max_value=10).filter(lambda x: True)
        gen = tuples(elem, integers())
        assert isinstance(gen, CompositeTupleGenerator)
        v = gen.generate()
        assert isinstance(v, tuple)
        assert len(v) == 2

    client.run_test("test_comp_tuple", test, test_cases=5)


def test_tuple_with_mapped_basic_generators(client):
    """Test tuples where all elements are BasicGenerators with transforms."""

    def test():
        gen1 = integers(min_value=0, max_value=10).map(lambda x: x * 2)
        gen2 = just(5).map(lambda x: x + 1)
        assert isinstance(gen1, BasicGenerator)
        assert isinstance(gen2, BasicGenerator)
        assert gen1._transform is not None
        assert gen2._transform is not None
        gen = tuples(gen1, gen2)
        assert isinstance(gen, BasicGenerator)
        assert gen._transform is not None
        v = gen.generate()
        assert isinstance(v, tuple)
        assert len(v) == 2
        assert 0 <= v[0] <= 20
        assert v[0] % 2 == 0
        assert v[1] == 6

    client.run_test("test_tuple_mapped", test, test_cases=5)


def test_composite_one_of_generator(client):
    """Test CompositeOneOfGenerator (generators without BasicGenerator schema)."""

    def test():
        gen = one_of(
            integers(min_value=0, max_value=10).filter(lambda x: True),
            text(),
        )
        assert isinstance(gen, CompositeOneOfGenerator)
        v = gen.generate()
        assert isinstance(v, int | str)

    client.run_test("test_comp_oneof", test, test_cases=10)


def test_composite_dict_generator(client):
    """Test CompositeDictGenerator (keys/values without BasicGenerator schema)."""

    def test():
        key_gen = text(min_size=1, max_size=3).filter(lambda x: True)
        gen = dicts(key_gen, integers(), min_size=0, max_size=2)
        assert isinstance(gen, CompositeDictGenerator)
        v = gen.generate()
        assert isinstance(v, dict)

    client.run_test("test_comp_dict", test, test_cases=5)


def test_composite_dict_no_max(client):
    """Test CompositeDictGenerator with no max_size."""

    def test():
        key_gen = text(min_size=1).filter(lambda x: True)
        gen = dicts(key_gen, integers())
        assert isinstance(gen, CompositeDictGenerator)
        v = gen.generate()
        assert isinstance(v, dict)

    client.run_test("test_comp_dict_nomax", test, test_cases=5)


def test_schema_dict_generator(client):
    """Test dicts with BasicGenerator keys and values."""

    def test():
        gen = dicts(text(min_size=1), integers(), min_size=0, max_size=2)
        assert isinstance(gen, BasicGenerator)
        v = gen.generate()
        assert isinstance(v, dict)

    client.run_test("test_schema_dict", test, test_cases=5)


def test_basic_dict_generator_schema():
    """Test dicts() returns BasicGenerator with schema."""
    gen = dicts(text(), integers(), min_size=0, max_size=5)
    assert isinstance(gen, BasicGenerator)
    assert gen.schema()["type"] == "dict"


# ---- one_of ----


def test_one_of_with_mapped_basic_generators():
    """Test one_of uses tagged tuples schema when BasicGenerators have transforms."""
    gen1 = just(1).map(lambda x: x * 2)
    gen2 = just(2).map(lambda x: x * 3)
    combined = one_of(gen1, gen2)
    assert isinstance(combined, BasicGenerator)
    schema = combined.schema()
    assert "one_of" in schema
    assert schema["one_of"][0]["type"] == "tuple"


def test_one_of_with_mapped_basic_generators_through_server(client):
    """Test one_of with tagged_one_of actually applies transforms correctly."""

    def test():
        gen1 = just(1).map(lambda x: x * 2)  # -> 2
        gen2 = just(2).map(lambda x: x * 3)  # -> 6
        combined = one_of(gen1, gen2)
        v = combined.generate()
        assert v in [2, 6]

    client.run_test("test_tagged_one_of", test, test_cases=10)


def test_one_of_with_non_basic_generators():
    """Test one_of falls back to compositional when not all generators are BasicGenerators."""
    gen1 = integers().filter(lambda x: x > 0)
    gen2 = integers()
    combined = one_of(gen1, gen2)
    assert isinstance(combined, CompositeOneOfGenerator)


# ---- sampled_from ----


def test_sampled_from_non_primitive(client):
    """Test sampled_from with non-primitive objects (identity preserved)."""

    class Custom:
        def __init__(self, x):
            self.x = x

    items = [Custom(1), Custom(2), Custom(3)]

    def test():
        gen = sampled_from(items)
        assert isinstance(gen, BasicGenerator)
        v = gen.generate()
        assert v in items

    client.run_test("test_sampled_nonprim", test, test_cases=10)


def test_sampled_from_empty_raises():
    """Test sampled_from raises ValueError for empty list."""
    with pytest.raises(ValueError, match="at least one element"):
        sampled_from([])


def test_sampled_from_with_objects():
    """Test sampled_from preserves object identity."""
    obj1 = object()
    obj2 = object()
    gen = sampled_from([obj1, obj2])
    schema = gen.schema()
    assert schema is not None
    assert schema["type"] == "integer"
    assert schema["min_value"] == 0
    assert schema["max_value"] == 1


# ---- binary() generator ----


def test_binary_generator(client):
    """Test binary() generator."""

    def test():
        gen = binary(min_size=1, max_size=10)
        v = gen.generate()
        assert isinstance(v, str)

    client.run_test("test_binary_gen", test, test_cases=5)


def test_binary_generator_no_max(client):
    """Test binary() generator without max_size."""

    def test():
        gen = binary()
        v = gen.generate()
        assert isinstance(v, str)

    client.run_test("test_binary_nomax", test, test_cases=5)


def test_binary_generator_schema():
    """Test binary() generator factory function."""
    gen = binary()
    assert isinstance(gen, BasicGenerator)
    assert gen.schema()["type"] == "binary"

    gen_with_max = binary(min_size=5, max_size=10)
    assert isinstance(gen_with_max, BasicGenerator)
    assert gen_with_max.schema()["min_size"] == 5
    assert gen_with_max.schema()["max_size"] == 10


# ---- optional() ----


def test_optional_generator(client):
    """Test optional() generator."""

    def test():
        gen = optional(integers(min_value=0, max_value=10))
        v = gen.generate()
        assert v is None or isinstance(v, int)

    client.run_test("test_optional", test, test_cases=20)


# ---- just() ----


def test_just_generator(client):
    """Test just() generator."""

    def test():
        gen = just(42)
        v = gen.generate()
        assert v == 42

    client.run_test("test_just", test, test_cases=5)


# ---- DataclassGenerator ----


def test_dataclass_generator(client):
    """Test DataclassGenerator."""

    @dataclass
    class Point:
        x: int
        y: int

    def test():
        gen = from_type(Point)
        assert isinstance(gen, BasicGenerator)
        v = gen.generate()
        assert isinstance(v, Point)

    client.run_test("test_dataclass", test, test_cases=5)


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


def test_dataclass_generator_compositional(client):
    """Test DataclassGenerator with fields that lack BasicGenerator schema."""

    @dataclass
    class Thing:
        x: int
        label: str

    def test():
        gen = DataclassGenerator(Thing)
        gen = gen.with_field("label", text().filter(lambda s: True))
        built = gen.build()
        assert not isinstance(built, BasicGenerator)
        v = built.generate()
        assert isinstance(v, Thing)

    client.run_test("test_dc_comp", test, test_cases=5)


def test_dataclass_not_a_dataclass():
    """Test DataclassGenerator rejects non-dataclass."""
    with pytest.raises(TypeError, match="is not a dataclass"):
        DataclassGenerator(int)


def test_dataclass_generator_basic_when_all_fields_basic():
    """Test DataclassGenerator builds a BasicGenerator when all fields are basic."""

    @dataclass
    class Point:
        x: int
        y: int

    gen = DataclassGenerator(Point)
    built = gen.build()
    assert isinstance(built, BasicGenerator)
    assert built.schema()["type"] == "object"
    assert "x" in built.schema()["properties"]
    assert "y" in built.schema()["properties"]


def test_dataclass_generator_non_basic_when_field_not_basic():
    """Test DataclassGenerator builds a non-basic generator when a field is not basic."""

    @dataclass
    class Point:
        x: int
        y: int

    gen = DataclassGenerator(Point)
    gen_override = gen.with_field("x", integers().filter(lambda x: True))
    built = gen_override.build()
    assert not isinstance(built, BasicGenerator)


def test_dataclass_with_composite_field_through_server(client):
    """Test DataclassGenerator with fields that have no schema."""

    @dataclass
    class Point:
        x: int
        y: int

    def test():
        gen = DataclassGenerator(Point)
        mapped_gen = integers().map(lambda x: x * 2)
        gen_with_override = gen.with_field("x", mapped_gen)
        result = gen_with_override.build().generate()
        assert isinstance(result, Point)

    client.run_test("test_dataclass_composite", test, test_cases=5)


# ---- from_type() paths ----


@pytest.mark.parametrize("tp", [type(None), bytes, float])
def test_from_type_returns_basic_generator(tp):
    assert isinstance(from_type(tp), BasicGenerator)


@pytest.mark.parametrize(
    "tp",
    [
        int | str,
        int | None,
        int | str | float,
        list[int],
        dict[str, int],
        tuple[int, str],
        set[int],
    ],
)
def test_from_type(tp):
    assert from_type(tp)


@pytest.mark.parametrize("tp", [list, dict, tuple, set])
def test_from_type_bare_raises(tp):
    with pytest.raises(TypeError, match="Cannot generate"):
        from_type(tp)


@pytest.mark.parametrize(
    "tp",
    [
        typing.List,  # noqa: UP006
        typing.Dict,  # noqa: UP006
        typing.Tuple,  # noqa: UP006
        typing.Set,  # noqa: UP006
    ],
)
def test_from_type_bare_typing_generic(tp):
    assert from_type(tp) is not None


def test_from_type_enum():
    class Color(Enum):
        RED = 1
        GREEN = 2
        BLUE = 3

    assert from_type(Color) is not None


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
        assert "test_sdk.py" in origin


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
        patch("hegel_sdk.session.sys") as mock_sys,
        patch("hegel_sdk.session.os.path.exists", return_value=True),
    ):
        mock_sys.prefix = str(tmp_path)
        mock_sys.base_prefix = "/different"
        result = _find_hegeld()
        assert "hegel" in result


def test_find_hegeld_in_venv_not_exists():
    """Test _find_hegeld when venv hegel doesn't exist."""
    with (
        patch("hegel_sdk.session.sys") as mock_sys,
        patch("hegel_sdk.session.os.path.exists", return_value=False),
        patch("hegel_sdk.session.shutil.which", return_value="/usr/local/bin/hegel"),
    ):
        mock_sys.prefix = "/some/venv"
        mock_sys.base_prefix = "/different"
        result = _find_hegeld()
        assert result == "/usr/local/bin/hegel"


def test_find_hegeld_in_path():
    """Test _find_hegeld finds hegel on PATH."""
    with (
        patch("hegel_sdk.session.sys") as mock_sys,
        patch("hegel_sdk.session.shutil.which", return_value="/usr/bin/hegel"),
    ):
        mock_sys.prefix = mock_sys.base_prefix = "/same"
        result = _find_hegeld()
        assert result == "/usr/bin/hegel"


def test_find_hegeld_fallback():
    """Test _find_hegeld falls back to python -m hegel."""
    with (
        patch("hegel_sdk.session.sys") as mock_sys,
        patch("hegel_sdk.session.shutil.which", return_value=None),
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

        def test():
            x = generate_from_schema(
                {"type": "integer", "min_value": 0, "max_value": 10}
            )
            assert isinstance(x, int)

        session.run_test(test, test_cases=5)
    finally:
        session._cleanup()


def test_hegel_session_start_verbose_double_check_lock():
    """Test _HegelSession._start double-check lock path."""
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
    """Test _HegelSession._start kills process on timeout."""
    session = _HegelSession()

    with (
        patch("hegel_sdk.session._find_hegeld", return_value=sys.executable),
        patch("hegel_sdk.session.subprocess.Popen") as mock_popen,
        patch("hegel_sdk.session.os.path.exists", return_value=False),
        patch("hegel_sdk.session.time.sleep"),
    ):
        mock_process = MagicMock()
        mock_popen.return_value = mock_process

        with pytest.raises(RuntimeError, match="Timeout"):
            session._start()

        mock_process.kill.assert_called_once()


def test_hegel_session_connection_retry():
    """Test _HegelSession._start retries on ConnectionRefusedError."""
    session = _HegelSession()

    with (
        patch("hegel_sdk.session._find_hegeld", return_value=sys.executable),
        patch("hegel_sdk.session.subprocess.Popen") as mock_popen,
        patch("hegel_sdk.session.os.path.exists", return_value=True),
        patch("hegel_sdk.session.socket.socket") as mock_socket_cls,
        patch("hegel_sdk.session.time.sleep"),
    ):
        mock_process = MagicMock()
        mock_popen.return_value = mock_process
        mock_sock = MagicMock()
        mock_sock.connect.side_effect = ConnectionRefusedError("not ready")
        mock_socket_cls.return_value = mock_sock

        with pytest.raises(RuntimeError, match="Timeout"):
            session._start()

        assert mock_sock.close.call_count > 0


# ---- start_span/stop_span when aborted ----


def test_start_stop_span_when_aborted():
    token_aborted = _test_aborted.set(True)
    token_channel = _current_channel.set(MagicMock())
    try:
        start_span(1)
        stop_span()
    finally:
        _test_aborted.reset(token_aborted)
        _current_channel.reset(token_channel)


# ---- Through-server integration tests ----


def test_strategy_helpers(client):
    def test():
        n = integers(min_value=0, max_value=10).generate()
        assert 0 <= n <= 10

        s = text(min_size=1, max_size=5).generate()
        assert 1 <= len(s) <= 5

        b = booleans().generate()
        assert isinstance(b, bool)

    client.run_test("test_helpers", test, test_cases=50)


def test_lists_of_integers(client):
    def test():
        xs = lists(integers(min_value=0, max_value=10), max_size=3).generate()
        assert isinstance(xs, list)
        assert len(xs) <= 3
        for x in xs:
            assert 0 <= x <= 10

    client.run_test("test_lists", test, test_cases=10)


def test_composite_list_generator_through_server(client):
    """Test CompositeListGenerator with live server."""

    def test():
        mapped = integers().map(lambda x: x * 2)
        result = lists(mapped, min_size=1, max_size=3).generate()
        assert isinstance(result, list)
        assert 1 <= len(result) <= 3

    client.run_test("test_composite_list", test, test_cases=5)


def test_composite_tuple_generator_through_server(client):
    """Test CompositeTupleGenerator with live server."""

    def test():
        filtered = integers().filter(lambda x: True)
        result = tuples(filtered, integers()).generate()
        assert isinstance(result, tuple)
        assert len(result) == 2

    client.run_test("test_composite_tuple", test, test_cases=5)


def test_composite_one_of_generator_through_server(client):
    """Test CompositeOneOfGenerator with live server."""

    def test():
        filtered = integers().filter(lambda x: True)
        result = one_of(filtered, text()).generate()
        assert isinstance(result, int | str)

    client.run_test("test_composite_one_of", test, test_cases=5)


def test_composite_dict_generator_through_server(client):
    """Test CompositeDictGenerator with live server."""

    def test():
        mapped_keys = text().map(lambda x: x.upper())
        result = dicts(mapped_keys, integers(), min_size=0, max_size=3).generate()
        assert isinstance(result, dict)

    client.run_test("test_composite_dict", test, test_cases=5)


def test_sampled_from_non_primitive_through_server(client):
    """Test sampled_from with non-primitives preserves identity."""
    obj_a = object()
    obj_b = object()

    def test():
        result = sampled_from([obj_a, obj_b]).generate()
        assert result is obj_a or result is obj_b

    client.run_test("test_sampled_non_primitive", test, test_cases=5)


def test_schema_dict_generator_through_server(client):
    """Test SchemaDictGenerator with live server."""

    def test():
        result = dicts(text(), integers(), min_size=0, max_size=2).generate()
        assert isinstance(result, dict)

    client.run_test("test_schema_dict", test, test_cases=5)


# ---- Error paths ----


def test_failing_test_single_interesting(client):
    """Test that a single failing test raises properly."""

    def test():
        x = generate_from_schema({"type": "integer", "min_value": 0, "max_value": 100})
        assert x < 50

    with pytest.raises(AssertionError):
        client.run_test("test_fail", test, test_cases=100)


def test_multiple_interesting_exception_group(client):
    """Test ExceptionGroup is raised when there are multiple interesting examples."""

    def test():
        x = generate_from_schema({"type": "integer", "min_value": 0, "max_value": 1000})
        if x < 500:
            assert x < 0, "first origin"
        else:
            assert x < 500, "second origin"

    with pytest.raises(ExceptionGroup):
        client.run_test("test_multi", test, test_cases=200)


def test_generate_from_schema_non_stop_test_error(client):
    """Test generate_from_schema re-raises non-StopTest RequestError."""

    def test():
        with pytest.raises(RequestError):
            generate_from_schema({"type": "completely_invalid_schema_type"})

    client.run_test("test_bad_schema", test, test_cases=1)


def test_connection_error_in_test_function(client):
    """Test ConnectionError is re-raised from test function."""

    def test():
        raise ConnectionError("test connection lost")

    with pytest.raises(ConnectionError, match="test connection lost"):
        client.run_test("test_conn_err", test, test_cases=1)


def test_unrecognised_event_in_run_test():
    """Test unrecognised event handling in Client.run_test."""

    server_socket, client_socket = socket.socketpair()
    server_conn = Connection(server_socket, name="Server")
    client_conn = Connection(client_socket, name="Client")

    def fake_server():
        server_conn.receive_handshake()
        control = server_conn.control_channel

        msg_id, message = control.receive_request()
        test_channel = server_conn.connect_channel(
            message["channel"],
            role="Test",
        )
        control.send_response_value(msg_id, message=True)

        req_id = test_channel.send_request({"event": "bogus_event"})
        test_channel.receive_response_raw(req_id)

        test_channel.request(
            {
                "event": "test_done",
                "results": {
                    "passed": True,
                    "test_cases": 0,
                    "valid_test_cases": 0,
                    "invalid_test_cases": 0,
                    "interesting_test_cases": 0,
                },
            },
        ).get()

    t = Thread(target=fake_server, daemon=True)
    t.start()

    try:
        c = Client(client_conn)
        c.run_test("test_bogus", lambda: None, test_cases=1)
    finally:
        client_conn.close()
        server_conn.close()
        t.join(timeout=5)


def test_is_final_pass_with_multiple_interesting(client):
    """Test the AssertionError when is_final test case passes with n_interesting > 1."""

    def test():
        x = generate_from_schema({"type": "integer", "min_value": 0, "max_value": 1000})
        if x < 500:
            assert x < 0
        else:
            assert x < 500

    original_run_test_case = Client._run_test_case
    is_final_count = [0]

    def patched_run_test_case(self, channel, test_fn, *, is_final):
        if is_final:
            is_final_count[0] += 1
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
        client.run_test("test_final_pass", test, test_cases=200)

    assert is_final_count[0] > 1


def test_nested_test_case_raises(client):
    """Test that nesting test cases raises RuntimeError."""

    def test():
        channel = _get_channel()
        with pytest.raises(RuntimeError, match="Cannot nest test cases"):
            client._run_test_case(channel, lambda: None, is_final=False)

    client.run_test("test_nested", test, test_cases=1)


def test_get_channel_outside_context():
    """Test _get_channel raises RuntimeError outside test context."""
    with pytest.raises(RuntimeError, match="Not in a test context"):
        _get_channel()


# ---- collection ----


def test_collection_more_after_finished(client):
    """Test collection.more() returns False when already finished."""

    def test():
        c = collection("test_coll", min_size=0, max_size=1)
        while c.more():
            generate_from_schema({"type": "integer"})
        assert c.more() is False

    client.run_test("test_more_finished", test, test_cases=5)


def test_collection_reject_while_active(client):
    """Test collection.reject() while collection is active."""

    def test():
        c = collection("test_coll", min_size=1, max_size=5)
        while c.more():
            val = generate_from_schema(
                {"type": "integer", "min_value": 0, "max_value": 100}
            )
            if val % 2 != 0:
                c.reject()

    client.run_test("test_reject_active", test, test_cases=10)


def test_collection_reject_when_finished(client):
    """Test collection.reject() is a no-op when collection is finished."""

    def test():
        c = collection("test_coll", min_size=0, max_size=1)
        while c.more():
            generate_from_schema({"type": "integer"})
        result = c.reject()
        assert result is None

    client.run_test("test_reject_finished", test, test_cases=5)
