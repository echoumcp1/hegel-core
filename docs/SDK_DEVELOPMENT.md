# Hegel SDK Development Guide

This document provides comprehensive guidance for implementing Hegel SDKs in new languages. It is designed for both human developers and AI assistants.

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Generation Modes](#generation-modes)
4. [Protocol Specification](#protocol-specification)
5. [Complete Feature List](#complete-feature-list)
6. [Design Considerations](#design-considerations)
7. [Error Handling](#error-handling)
8. [Testing Patterns](#testing-patterns)
9. [Implementation Checklist](#implementation-checklist)

---

## Overview

A Hegel SDK enables property-based testing from any language by communicating with the Hegel server via Unix socket. The SDK provides:

1. **Generator types** that produce random values according to constraints
2. **Strategies API** with combinators for composing generators
3. **Socket communication** to request values from Hypothesis's generation engine
4. **Schema composition** for efficient single-request generation of complex structures

### Core Principles

- **Schema composition preferred**: When possible, compose JSON schemas and make a single socket request
- **Compositional fallback**: When schemas unavailable (after `map`/`filter`), generate structurally with multiple requests
- **Idiomatic APIs**: Match the target language's conventions for generics, builders, and error handling
- **Minimal overhead**: Reuse connections, batch requests via schema composition

---

## Architecture

### Environment Variables

Test binaries receive these environment variables from Hegel:

| Variable | Required | Description |
|----------|----------|-------------|
| `HEGEL_SOCKET` | Yes | Path to Unix socket for communication |
| `HEGEL_REJECT_CODE` | Yes | Exit code for rejected test cases (default: 137) |
| `HEGEL_DEBUG` | No | Enable debug logging when set |

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Test passed |
| 1 | Test failed (assertion failure) |
| 134 | Socket communication error (recommended) |
| `HEGEL_REJECT_CODE` | Test case rejected (invalid input) |

### Connection Lifecycle

```
┌─────────────────────────────────────────────────────────────┐
│                    Standalone Mode                          │
├─────────────────────────────────────────────────────────────┤
│  1. First generate() or start_span() opens connection       │
│  2. Connection persisted across multiple generate() calls   │
│  3. Connection closed when span_depth reaches 0             │
│  4. Thread-local state for multi-threaded tests             │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                    Embedded Mode                            │
├─────────────────────────────────────────────────────────────┤
│  1. Connection provided by embedding runtime                │
│  2. SDK uses provided file descriptor                       │
│  3. No connection management needed                         │
│  4. Rejection via exception instead of exit                 │
└─────────────────────────────────────────────────────────────┘
```

---

## Generation Modes

SDKs must support two generation modes, automatically selecting the optimal one.

### Mode 1: Schema Composition (Preferred)

When all child generators have JSON schemas, compose them into a single schema and make one socket request.

**Advantages:**
- Single socket round-trip for complex nested structures
- Better shrinking (Hegel understands full structure)
- More efficient generation

**Example: Generating a list of integers**

```
Schema Request:
{
  "type": "array",
  "items": {"type": "integer", "minimum": 0, "maximum": 100},
  "minItems": 1,
  "maxItems": 10
}

Single Response:
[42, 17, 83, 5]
```

**When available:**
- Primitive generators (`integers`, `text`, `booleans`, etc.)
- Collections of generators with schemas
- Struct/object generators with all fields having schemas
- `one_of` with all variants having schemas

### Mode 2: Compositional Generation (Fallback)

When schemas are unavailable, generate the structure piece by piece.

**Required when:**
- After `map()` - transformation destroys type information
- After `filter()` - predicate not expressible in JSON Schema
- After `flatmap()` - dependent generation
- Custom generators without schemas

**Example: Generating a list without schema**

```
Request 1: {"type": "integer", "minimum": 1, "maximum": 10}  // length
Response 1: 3

Request 2: <element schema>  // element 0
Response 2: 42

Request 3: <element schema>  // element 1
Response 3: 17

Request 4: <element schema>  // element 2
Response 4: 83
```

### Span Labels for Compositional Generation

Use labeled spans to help Hegel understand structure for shrinking:

| Label | Usage |
|-------|-------|
| `list` | Wraps list generation |
| `list_element` | Wraps each element |
| `optional` | Wraps optional value generation |
| `filter` | Wraps filtered generation (can be discarded) |
| `one_of` | Wraps variant selection |

---

## Protocol Specification

### Request Format

Newline-delimited JSON:

```json
{"id": <integer>, "command": "<string>", "payload": <json>}\n
```

### Commands

#### `generate`

Request value generation from a JSON Schema.

```json
{"id": 1, "command": "generate", "payload": {"type": "integer", "minimum": 0}}
```

Response:
```json
{"id": 1, "result": 42}
```

#### `start_span`

Begin a labeled span for structural grouping.

```json
{"id": 2, "command": "start_span", "payload": "list"}
```

Response:
```json
{"id": 2, "result": null}
```

#### `stop_span`

End the current span, optionally discarding generated data.

```json
{"id": 3, "command": "stop_span", "payload": false}
```

Payload is `true` to discard (e.g., filtered value rejected), `false` to keep.

Response:
```json
{"id": 3, "result": null}
```

### Response Format

Success:
```json
{"id": <integer>, "result": <json>}
```

Error:
```json
{"id": <integer>, "error": "<message>"}
```

### Important Notes

- **Request ID matching**: Always verify response ID matches request ID
- **Newline termination**: Every message must end with `\n`
- **Buffered reading**: Handle partial reads; accumulate until newline found
- **additionalProperties**: Server automatically adds `"additionalProperties": false` to object schemas—SDKs should NOT add this

---

## Complete Feature List

### Core Generator Type

The fundamental type that all strategies return:

```
Generator<T>
├── generate() -> T           // Produce a value
├── schema() -> Option<JSON>  // JSON Schema if available
├── map(f: T -> U) -> Generator<U>
├── flatmap(f: T -> Generator<U>) -> Generator<U>  // or flat_map
├── filter(pred: T -> bool, max_attempts=3) -> Generator<T>
└── boxed() -> BoxedGenerator<T>  // Type erasure (if needed)
```

### Strategies Namespace/Module

All generator functions live in a `strategies` (or `st`, `gen`) namespace.

#### Primitives

| Function | Schema | Notes |
|----------|--------|-------|
| `nulls()` | `{"type": "null"}` | Generates null/nil/None |
| `booleans()` | `{"type": "boolean"}` | Generates true/false |
| `just(value)` | `{"const": value}` | Always returns the same value |

#### Numeric

| Function | Parameters | Schema |
|----------|------------|--------|
| `integers<T>()` | `min_value`, `max_value` | `{"type": "integer", "minimum": N, "maximum": M}` |
| `floats<T>()` | `min_value`, `max_value`, `exclude_min`, `exclude_max` | `{"type": "number", ...}` |

**Static language notes:**
- Template/generic on return type: `integers<uint8_t>()` auto-derives bounds 0-255
- Use language's numeric limits as defaults

**Float exclusions:**
- `exclude_min: true` → `"exclusiveMinimum": min_value`
- `exclude_max: true` → `"exclusiveMaximum": max_value`

#### Strings

| Function | Parameters | Schema |
|----------|------------|--------|
| `text()` | `min_size`, `max_size` | `{"type": "string", "minLength": N, "maxLength": M}` |
| `from_regex(pattern)` | pattern string | `{"type": "string", "pattern": "^...$"}` |

**from_regex notes:**
- Auto-anchor: if pattern doesn't start with `^`, prepend it; if doesn't end with `$`, append it
- Pattern must be valid JSON Schema regex (subset of ECMA-262)

#### Format Strings

| Function | Parameters | Schema Format |
|----------|------------|---------------|
| `emails()` | none | `"format": "email"` |
| `urls()` | none | `"format": "uri"` |
| `domains()` | `max_length` | `"format": "hostname"` |
| `ip_addresses()` | `v=4\|6\|None` | `"format": "ipv4"` or `"ipv6"` |
| `dates()` | none | `"format": "date"` |
| `times()` | none | `"format": "time"` |
| `datetimes()` | none | `"format": "date-time"` |

**Not supported:** uuid, duration (Hypothesis doesn't support these formats)

#### Collections

| Function | Parameters | Notes |
|----------|------------|-------|
| `lists(elements)` | `min_size`, `max_size`, `unique` | Name idiomatically: `vectors`, `arrays`, `vecs` |
| `sets(elements)` | `min_size`, `max_size` | Generate as unique list, convert to set |
| `dictionaries(keys, values)` | `min_size`, `max_size` | **Keys must be strings** (JSON limitation) |
| `tuples(gen1, gen2, ...)` | generators | Fixed-length heterogeneous; use `prefixItems` schema |

**Collection schema composition:**

```json
// lists(integers().with_min(0), min_size=1, max_size=5)
{
  "type": "array",
  "items": {"type": "integer", "minimum": 0},
  "minItems": 1,
  "maxItems": 5
}

// lists(..., unique=true)
{
  "type": "array",
  "items": {...},
  "uniqueItems": true
}

// tuples(integers(), text())
{
  "type": "array",
  "prefixItems": [
    {"type": "integer"},
    {"type": "string"}
  ],
  "items": false,
  "minItems": 2,
  "maxItems": 2
}
```

#### Combinators

| Function | Description | Schema |
|----------|-------------|--------|
| `sampled_from(elements)` | Uniform selection from fixed collection | `{"enum": [...]}` |
| `one_of(generators...)` | Choose from homogeneous generators | `{"anyOf": [...]}` |
| `optional(generator)` | None/null or a value | `{"anyOf": [{"type": "null"}, ...]}` |
| `builds(type, generators...)` | Construct objects from generated values | Compose field schemas |

#### Dynamic Language Only

| Function | Description |
|----------|-------------|
| `fixed_dictionaries({"key": gen, ...})` | Dict with fixed keys, generated values |

This is essential for dynamic languages to generate struct-like data without actual struct types.

### Object/Struct Generation

#### Static Languages

Two patterns for struct generation:

**1. Default Generator (schema from type reflection)**
```cpp
// C++ with reflect-cpp
auto gen = default_generator<Person>();
Person p = gen.generate();
```

**2. Builder Pattern with Field Generators**
```rust
// Rust with derive macro
let gen = PersonGenerator::new()
    .with_name(text().with_max_length(50))
    .with_age(integers::<u32>().with_max(120));
let p: Person = gen.generate();
```

#### Dynamic Languages

Use `fixed_dictionaries`:
```python
person_gen = fixed_dictionaries(
    {"name": text(max_size=50), "age": integers(min_value=0, max_value=120)}
)
```

### Schema for Objects

```json
{
  "type": "object",
  "properties": {
    "name": {"type": "string", "maxLength": 50},
    "age": {"type": "integer", "minimum": 0, "maximum": 120}
  },
  "required": ["name", "age"]
}
```

**Note:** Server adds `"additionalProperties": false` automatically.

---

## Design Considerations

### Language-Specific Idioms

Before implementing, understand the target language's patterns for:

| Concept | Questions to Answer |
|---------|---------------------|
| **Generics** | Templates? Type parameters? Protocols? |
| **Higher-order functions** | Closures? Lambdas? Function types? |
| **Option types** | `Option<T>`? `Maybe`? `null`? `nil`? |
| **Object construction** | Constructors? Builders? Factory functions? |
| **Collections** | List vs Vector vs Array naming? Set types? |
| **Error handling** | Exceptions? Result types? Error codes? |

### API Style Patterns

#### Builder Pattern (Rust style)
```rust
integers::<i32>()
    .with_min(0)
    .with_max(100)
    .generate()
```

#### Parameter Structs (C++ style)
```cpp
integers<int>({.min_value = 0, .max_value = 100})
```

#### Keyword Arguments (Python style)
```python
integers(min_value=0, max_value=100)
```

#### Fluent Interface (Java/Kotlin style)
```kotlin
Generators.integers()
    .between(0, 100)
    .generate()
```

### Type Parameter Conventions

**Static languages with bounds inference:**
```rust
integers::<u8>()  // Automatically min=0, max=255
integers::<i32>().with_min(-1000).with_max(1000)  // Override defaults
```

**Dynamic languages:**
```python
integers()  # Arbitrary precision
integers(min_value=-128, max_value=127)  # Explicit bounds
```

### Naming Conventions

| Concept | Examples by Language Style |
|---------|---------------------------|
| Lists | `vectors` (C++), `vecs` (Rust), `lists` (Python), `arrays` (JS) |
| Maps | `dictionaries` (Python), `hashmaps` (Rust), `maps` (Java) |
| Optional | `optional` (C++), `optional` (Rust), `none_or` (Hypothesis) |

### Thread Safety

- Use thread-local storage for connection state
- Atomic operations for request ID counter
- Each thread maintains independent connection

```rust
thread_local! {
    static CONNECTION: RefCell<Option<ConnectionState>> = RefCell::new(None);
}
static REQUEST_ID: AtomicU64 = AtomicU64::new(0);
```

### UTF-8 and String Lengths

JSON Schema string lengths count **Unicode codepoints**, not bytes:

```json
{"type": "string", "minLength": 1, "maxLength": 10}
```

This means 1-10 codepoints. If your language counts bytes by default (e.g., C), you need codepoint-aware string handling.

---

## Error Handling

### The assume() Function

Every SDK must implement `assume(condition)`:

```
assume(condition: bool) -> void
├── If condition is true: returns normally
├── If condition is false:
│   ├── Standalone mode: exit with HEGEL_REJECT_CODE
│   └── Embedded mode: throw exception / panic with special marker
```

**Purpose:** Signal that the current test case is invalid (not a failure). Hegel will try different inputs.

**Common rejection scenarios:**
- Filter predicate failed max attempts
- Generated value doesn't meet preconditions
- JSON parsing failed (malformed response)

### Error Categories

| Error Type | Action |
|------------|--------|
| Socket connection failure | Exit with SOCKET_ERROR (134) |
| Socket I/O error | Exit with SOCKET_ERROR (134) |
| JSON parse error | `assume(false)` |
| Server returns error | `assume(false)` |
| Filter exhaustion | `assume(false)` |
| Test assertion failure | Exit with code 1 |

### Filter Implementation

```python
def filter(self, predicate, max_attempts=3):
    def generate():
        for _ in range(max_attempts):
            value = self.generate()
            if predicate(value):
                return value
        assume(False)

    return Generator(generate)
```

With spans for proper shrinking:
```rust
fn generate(&self) -> T {
    for _ in 0..self.max_attempts {
        if let Some(value) = discardable_group(labels::FILTER, || {
            let value = self.source.generate();
            if (self.predicate)(&value) {
                Some(value)
            } else {
                None  // Triggers span discard
            }
        }) {
            return value;
        }
    }
    assume(false);
    unreachable!()
}
```

### Embedded Mode Exceptions

When running embedded (SDK provides the Hegel server), assume(false) uses exceptions:

```cpp
// C++
class HegelReject : public std::exception {
public:
    const char* what() const noexcept override { return "assume failed"; }
};

void assume(bool condition) {
    if (!condition) {
        if (current_mode == Mode::Embedded) {
            throw HegelReject();
        } else {
            std::exit(get_reject_code());
        }
    }
}
```

```rust
// Rust - use panic with special marker
const REJECT_MARKER: &str = "HEGEL_REJECT";

pub fn assume(condition: bool) {
    if !condition {
        match current_mode() {
            HegelMode::Embedded => panic!("{}", REJECT_MARKER),
            HegelMode::Standalone => {
                std::process::exit(get_reject_code());
            }
        }
    }
}
```

---

## Testing Patterns

### Test Selection Pattern

Use `sampled_from` to let Hegel explore different test paths:

```rust
fn main() {
    let tests: Vec<(&str, fn())> = vec![
        ("test_integers", test_integers),
        ("test_strings", test_strings),
        ("test_lists", test_lists),
    ];

    let names: Vec<_> = tests.iter().map(|(n, _)| *n).collect();
    let selected = sampled_from(names).generate();

    for (name, test_fn) in &tests {
        if *name == selected {
            test_fn();
            break;
        }
    }
}
```

### Property Test Structure

```rust
fn test_list_reverse() {
    // Generate inputs
    let list = vecs(integers::<i32>())
        .with_min_size(0)
        .with_max_size(100)
        .generate();

    // Property: reversing twice gives original
    let reversed_twice: Vec<i32> = list.iter()
        .rev()
        .rev()
        .cloned()
        .collect();

    assert_eq!(list, reversed_twice);
}
```

### Debugging Output

Use `note()` or equivalent for debugging output visible during shrinking:

```rust
fn test_sorting() {
    let list = vecs(integers::<i32>()).generate();
    note(&format!("Testing with list: {:?}", list));

    let sorted = sort(&list);
    assert!(is_sorted(&sorted));
}
```

---

## Implementation Checklist

### Phase 1: Core Infrastructure

- [ ] Socket connection management (thread-local)
- [ ] Request/response JSON serialization
- [ ] Request ID counter (atomic)
- [ ] `assume()` function
- [ ] Environment variable reading (`HEGEL_SOCKET`, `HEGEL_REJECT_CODE`)
- [ ] Basic `Generator<T>` type with `generate()` and `schema()`

### Phase 2: Primitive Generators

- [ ] `nulls()`
- [ ] `booleans()`
- [ ] `just(value)`
- [ ] `integers()` with min/max
- [ ] `floats()` with min/max and exclusions
- [ ] `text()` with length bounds
- [ ] `from_regex(pattern)` with auto-anchoring

### Phase 3: Format Strings

- [ ] `emails()`
- [ ] `urls()`
- [ ] `domains()`
- [ ] `ip_addresses()` with v4/v6 option
- [ ] `dates()`
- [ ] `times()`
- [ ] `datetimes()`

### Phase 4: Collections

- [ ] `lists(elements)` with min/max size and unique
- [ ] `sets(elements)`
- [ ] `dictionaries(keys, values)` (string keys only)
- [ ] `tuples(generators...)`
- [ ] Schema composition for all collections
- [ ] Compositional fallback when schemas unavailable

### Phase 5: Combinators

- [ ] `sampled_from(elements)`
- [ ] `one_of(generators...)`
- [ ] `optional(generator)`
- [ ] `builds(type, generators...)`
- [ ] `map(f)` on Generator
- [ ] `flatmap(f)` / `flat_map(f)` on Generator
- [ ] `filter(predicate, max_attempts)` on Generator

### Phase 6: Advanced Features

- [ ] Span support (`start_span`, `stop_span`)
- [ ] Labeled groups for compositional generation
- [ ] Discardable spans for filtering
- [ ] Default generators from types (if language supports reflection)
- [ ] Builder pattern for customizing generators
- [ ] Derive macros / code generation (if applicable)

### Phase 7: Embedded Mode (Optional)

- [ ] Mode detection (standalone vs embedded)
- [ ] Exception-based assume(false) handling
- [ ] Provided file descriptor support
- [ ] Test runner integration

### Phase 8: Testing & Documentation

- [ ] Unit tests for each generator type
- [ ] Integration test with actual Hegel server
- [ ] Property tests using the SDK itself
- [ ] API documentation
- [ ] Usage examples

### Phase 9: Repository Integration (Required)

**An SDK is not complete until it is integrated into `just test`.**

- [ ] Add SDK to `CLAUDE.md` repository structure
- [ ] Add build commands to `CLAUDE.md`
- [ ] Add SDK description to SDK Generator Pattern section in `CLAUDE.md`
- [ ] Create `_<language>-sdk-tests` recipe in `Justfile`
- [ ] Add the recipe to the main `test` recipe in `Justfile`
- [ ] Update `clean` recipe in `Justfile` to remove SDK build artifacts
- [ ] Verify `just test` passes with the new SDK

---

## Reference Implementations

- **C++ SDK**: `sdks/cpp/hegel.hpp` and `sdks/cpp/hegel.cpp`
- **Rust SDK**: `sdks/rust/src/gen.rs` and `sdks/rust/src/lib.rs`
- **Go SDK**: `sdks/go/*.go` (modular implementation)
- **TypeScript SDK**: `sdks/typescript/src/*.ts` (modular implementation)

All implement the full feature set and can serve as reference for new implementations.
