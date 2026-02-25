# hegel-core

Universal property-based testing, backed by [Hypothesis](https://hypothesis.works/).

Hegel generates random inputs for your tests, finds failures, and automatically
shrinks them to minimal counterexamples. This repository contains the Hegel
server, CLI, and the reference Python SDK.

## Installation

```bash
pip install "hegel-sdk @ git+ssh://git@github.com/antithesishq/hegel-core.git#subdirectory=sdk"
```

The SDK requires the `hegel` server:

```bash
pip install "hegel @ git+ssh://git@github.com/antithesishq/hegel-core.git"
```

## Quick Start

```python
from hegel_sdk import hegel, integers


@hegel
def test_addition_commutative():
    a = integers(-1000, 1000).generate()
    b = integers(-1000, 1000).generate()
    assert a + b == b + a
```

Run with `pytest` as normal. Hegel generates 100 random input pairs and reports
the minimal counterexample if it finds one.

For a full walkthrough, see [docs/getting-started.md](docs/getting-started.md).

## SDKs

- [Python](https://github.com/antithesishq/hegel-core) (this repository)
- [Go](https://github.com/antithesishq/hegel-go)
- [TypeScript](https://github.com/antithesishq/hegel-typescript)
- [Rust](https://github.com/antithesishq/hegel-rust)
- [C++](https://github.com/antithesishq/hegel-cpp)
- [OCaml](https://github.com/antithesishq/hegel-ocaml)

## Development

```bash
just setup     # Install dependencies
just check     # Full CI: lint + typecheck + coverage
just test      # Run tests only
just format    # Auto-format code
```
