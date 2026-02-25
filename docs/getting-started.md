# Getting Started with Hegel (Python)

This tutorial walks through the main features of the Hegel Python SDK.

## Install Hegel

```bash
pip install hegel
```

## Write your first test

Create `example.py`:

```python
from hegel_sdk import hegel, integers


@hegel
def test_integers():
    n = integers().generate()
    print(f"called with {n}")
    assert isinstance(n, int)
```

The `@hegel` decorator marks a function as a property-based test. Inside the test
body, call `.generate()` on a generator to produce a random value. When the test
runs, Hegel generates random inputs and checks that the body never raises an
exception.

By default Hegel runs **100 test cases**. Override this with the `test_cases`
parameter:

```python
@hegel(test_cases=500)
def test_integers_many():
    n = integers().generate()
    assert isinstance(n, int)
```

You can also run a test programmatically with `run_hegel_test`:

```python
from hegel_sdk import integers, run_hegel_test


def my_test():
    n = integers().generate()
    assert isinstance(n, int)


run_hegel_test(my_test, test_cases=200)
```

## Running in a test suite

Hegel integrates with pytest. Name your function `test_*` and decorate with
`@hegel`:

```python
from hegel_sdk import hegel, integers


@hegel
def test_bounded_integers():
    n = integers(min_value=0, max_value=200).generate()
    assert n < 50  # this will fail!
```

When a test fails, Hegel shrinks the counterexample to the smallest value that
still triggers the failure -- here it will report `n = 50`.

Run the test with:

```bash
pytest example.py
```

## Generating multiple values

Call `.generate()` multiple times to produce multiple values in a single test:

```python
from hegel_sdk import hegel, integers, text


@hegel
def test_multiple_values():
    n = integers().generate()
    s = text().generate()
    assert isinstance(n, int)
    assert isinstance(s, str)
```

Unlike Hypothesis's `@given` decorator, which requires all inputs to be declared
upfront as function parameters, Hegel lets you call `.generate()` at any point
inside the test body -- including conditionally or in loops.

## Filtering

Use `.filter()` for simple conditions on a generator:

```python
from hegel_sdk import hegel, integers


@hegel
def test_even_integers():
    n = integers().filter(lambda x: x % 2 == 0).generate()
    assert n % 2 == 0
```

For conditions that depend on multiple generated values, use `assume()` inside
the test body:

```python
from hegel_sdk import assume, hegel, integers


@hegel
def test_division():
    n1 = integers(min_value=-1000, max_value=1000).generate()
    n2 = integers(min_value=-1000, max_value=1000).generate()
    assume(n2 != 0)  # skip this test case if n2 is zero

    q, r = divmod(n1, n2)
    assert n1 == q * n2 + r
```

Using bounds and `.map()` is more efficient than `.filter()` or `assume()` because
they avoid generating values that will be rejected.

## Transforming generated values

Use `.map()` to apply a function to each generated value:

```python
from hegel_sdk import hegel, integers


@hegel
def test_string_of_digits():
    s = integers(min_value=0, max_value=100).map(str).generate()
    assert s.isdigit()
```

## Dependent generation

Because generation is imperative in Hegel, you can use earlier results to
configure later generators directly:

```python
from hegel_sdk import hegel, integers, lists


@hegel
def test_list_with_valid_index():
    n = integers(min_value=1, max_value=10).generate()
    lst = lists(integers(), min_size=n, max_size=n).generate()
    index = integers(min_value=0, max_value=n - 1).generate()
    assert 0 <= index < len(lst)
```

You can also use `.flat_map()` for dependent generation within a single generator
expression:

```python
from hegel_sdk import hegel, integers, lists


@hegel
def test_flatmap_example():
    result = (
        integers(min_value=1, max_value=5)
        .flat_map(lambda n: lists(integers(), min_size=n, max_size=n))
        .generate()
    )
    assert 1 <= len(result) <= 5
```

## What you can generate

### Primitive types

```python
from hegel_sdk import binary, booleans, floats, integers, text

booleans()  # True or False
integers()  # arbitrary-precision integer
integers(min_value=0, max_value=100)  # bounded integer
floats()  # floating-point number
floats(min_value=0.0, max_value=1.0)  # bounded float
floats(allow_nan=False, allow_infinity=False)  # finite floats only
text()  # Unicode string
text(min_size=1, max_size=50)  # bounded-length string
binary()  # bytes
binary(min_size=4, max_size=16)  # bounded-length bytes
```

### Constants and choices

```python
from hegel_sdk import just, sampled_from

just(42)  # always returns 42
sampled_from(["red", "green", "blue"])  # picks from a list
```

### Collections

```python
from hegel_sdk import dicts, integers, lists, text, tuples

lists(integers())  # list of integers
lists(integers(), min_size=1, max_size=10)  # bounded-length list
dicts(text(max_size=10), integers())  # dict with string keys
dicts(text(), integers(), min_size=1, max_size=5)  # bounded-size dict
tuples(integers(), text())  # (int, str) tuple
```

### Combinators

```python
from hegel_sdk import integers, one_of, optional, text

one_of(integers(), text())  # value from either generator
optional(integers())  # None or an integer
gen.map(f)  # transform generated values
gen.filter(predicate)  # keep only matching values
gen.flat_map(f)  # dependent generation
```

### Formats and patterns

```python
from hegel_sdk import dates, datetimes, domains, emails, from_regex, ip_addresses, times, urls

emails()  # email addresses
urls()  # URLs
domains()  # domain names
dates()  # ISO 8601 date strings (YYYY-MM-DD)
times()  # ISO 8601 time strings
datetimes()  # ISO 8601 datetime strings
ip_addresses()  # IPv4 or IPv6 addresses
from_regex(r"[a-z]{3}-[0-9]{3}")  # strings matching a regex
```

## Type-directed derivation

Use `from_type()` to automatically derive a generator from a Python type hint:

```python
from hegel_sdk import from_type, hegel


@hegel
def test_from_type():
    n = from_type(int).generate()
    assert isinstance(n, int)

    s = from_type(str).generate()
    assert isinstance(s, str)
```

`from_type` supports `int`, `float`, `str`, `bool`, `bytes`, `list[T]`,
`dict[K, V]`, `tuple[T1, T2, ...]`, `Optional[T]`, `Union[...]`, dataclasses,
and enums:

```python
from dataclasses import dataclass

from hegel_sdk import from_type, hegel


@dataclass
class User:
    name: str
    age: int
    active: bool


@hegel
def test_user():
    user = from_type(User).generate()
    assert isinstance(user.name, str)
    assert isinstance(user.age, int)
```

For finer control over individual fields, use `from_type` with `.map()`:

```python
from hegel_sdk import from_type, hegel, integers, text


@hegel
def test_bounded_user():
    user = from_type(User).generate()
    assert isinstance(user.name, str)
    assert isinstance(user.age, int)
```

## Debugging with note()

Use `note()` to print debug information. Messages appear only when Hegel replays
the minimal failing example:

```python
from hegel_sdk import hegel, integers, note


@hegel
def test_with_notes():
    x = integers().generate()
    y = integers().generate()
    note(f"trying x={x}, y={y}")
    assert x + y == y + x  # commutativity -- always true
```

## Guiding generation with target()

Use `target()` to nudge Hegel toward interesting values, making it more likely to
find boundary failures:

```python
from hegel_sdk import hegel, integers, target


@hegel(test_cases=1000)
def test_seek_large_values():
    x = integers(min_value=0, max_value=10000).generate()
    target(x, label="maximize_x")
    assert x <= 9999
```

`target()` is advisory -- Hegel will try to maximize the targeted metric, but it
may still explore other regions of the input space.
