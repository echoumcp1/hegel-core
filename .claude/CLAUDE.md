# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Test Commands

```bash
pip install --group dev -e .  # Install with dev dependencies
pytest                        # Run all tests
pytest tests/test_parser.py   # Run single test file
pytest -k test_name           # Run tests matching pattern
ruff check .                  # Lint
shed                          # Format
mypy src/                     # Type check
```

## Architecture

This is the Python core of Hegel - the CLI and server that powers property-based testing across languages.

### Module Overview

- `__main__.py` - CLI entry point (`hegel` command), Hypothesis ConjectureRunner integration, socket server for external mode, client mode for embedded SDKs
- `runner.py` - Subprocess management, Unix socket server creation, request/response handling
- `parser.py` - Converts JSON schemas from SDKs into Hypothesis strategies
- `conformance.py` - Framework for testing SDK implementations against specification
- `tui.py` - Textual-based terminal UI for real-time test visualization

### Two Operating Modes

**External mode** (default): Hegel spawns the test binary as a subprocess, creates a Unix socket, and the SDK connects to it. The socket path is passed via `HEGEL_SOCKET` environment variable.

**Client mode** (`--client-mode`): The SDK creates the socket server, and Hegel connects as a client. Used for embedded/in-process testing where the SDK controls the test lifecycle.

### Generation Flow

1. SDK sends JSON schema via socket (e.g., `{"type": "integer", "minimum": 0}`)
2. `parser.py` converts schema to Hypothesis strategy via `from_schema()`
3. `ConjectureData.draw()` generates value from strategy
4. Result sent back to SDK as JSON

Schemas are cached by SHA1 hash in `FROM_SCHEMA_CACHE` for performance.

### Span Tracking

SDKs use `start_span`/`stop_span` commands to mark generation boundaries. This enables Hypothesis to understand structure for better shrinking. Spans can be discarded (for filtered values that don't pass predicates).

## Code Style

- Don't add message strings to pytest asserts (`assert x, "message"`). Pytest provides excellent error messages automatically.
