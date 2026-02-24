# Changelog

## 0.1.4 - 2026-02-24

Fix JSONL parsing in conformance tests to split only on `\n` instead of using `splitlines()`, which incorrectly splits on Unicode line boundary characters (`\x85`, `\u2028`, `\u2029`) that are valid inside JSON string values.


## 0.1.3 - 2026-02-24

This patch adds support for setting seed to the protocol.


## 0.1.2 - 2026-02-24

Refactor internal protocol code.


## 0.1.1 - 2026-02-23

Testing auto-release behavior, and many protocol changes from `0.1.0`.

