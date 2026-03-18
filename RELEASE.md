RELEASE_TYPE: minor

This release adds support `HealthCheck` to the protocol. A health check is a proactive error raised by Hegel when we detect your test is likely to have degraded testing power or performance. The protocol now communicates health check errors back to the client as a result packet with the `health_check_failure` key set, and supports clients setting `suppress_health_check` in the `run_test` payload.

As a result, this release also bumps the protocol version to 0.5.
