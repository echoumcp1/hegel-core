# Changelog

## 0.2.1 - 2026-03-18

Hegel currently requires tests to be fully deterministic in their data generation, because Hypothesis does, but was not previously correctly reporting Hypothesis's flaky test errors back to the client (A test is flaky if it doesn't successfully replay - that is, when rerun with the same data generation, a different result is produced).

This release adds protocol support for reporting those flaky errors back to the client.

## 0.2.0 - 2026-03-18

This release adds support `HealthCheck` to the protocol. A health check is a proactive error raised by Hegel when we detect your test is likely to have degraded testing power or performance. The protocol now communicates health check errors back to the client as a result packet with the `health_check_failure` key set, and supports clients setting `suppress_health_check` in the `run_test` payload.

As a result, this release also bumps the protocol version to 0.5.

## 0.1.2 - 2026-03-17

Internal refactoring and documentation.

## 0.1.1 - 2026-03-17

The reader loop now exits gracefully when the remote end closes the connection, instead of raising an unhandled exception in the reader thread.

## 0.1.0 - 2026-03-13

Initial release!
