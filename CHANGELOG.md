# Changelog

## 0.4.0 - 2026-03-11

Add absolute barebones requirement for Antithesis support: If the ANTITHESIS_OUTPUT_DIR
environment variable is set (indicating that we are running on the Antithesis system),
use the hypothesis-urandom backend, which will get its entropy from the Antithesis fuzzer.

## 0.3.6 - 2026-03-10

Rename `test` to `database_key` in the `run_test` command, and change its type from `string` to `bytes | None`.

## 0.3.5 - 2026-03-10

Switch packet reader from a co-operative concurrency model to a background thread reader.

## 0.3.4 - 2026-03-09

Avoid creation of `.hypothesis` directory on disk, preferring `.hegel`.

## 0.3.3 - 2026-03-05

Remove code that was only for the client.

## 0.3.2 - 2026-03-02

In the protocol, remove `{"type": "object"}` and rename `{"type": "number"}` to `{"type": "float"}`.


## 0.3.1 - 2026-03-02

Remove `{"type": "set"}` from the protocol in favor of `{"type": "list", "unique": True}`.

## 0.3.0 - 2026-02-27

This release adds a stateful testing API and makes Generator a generic type.

## 0.2.0 - 2026-02-27

Add format generators to the Python SDK: `emails`, `urls`, `domains`, `dates`, `times`, `datetimes`, `ip_addresses`, and `from_regex`. These provide convenient ways to generate structured string data in property-based tests.

## 0.1.4 - 2026-02-24

Fix JSONL parsing in conformance tests to split only on `\n` instead of using `splitlines()`, which incorrectly splits on Unicode line boundary characters (`\x85`, `\u2028`, `\u2029`) that are valid inside JSON string values.

## 0.1.3 - 2026-02-24

This patch adds support for setting seed to the protocol.

## 0.1.2 - 2026-02-24

Refactor internal protocol code.

## 0.1.1 - 2026-02-23

Testing auto-release behavior, and many protocol changes from `0.1.0`.

