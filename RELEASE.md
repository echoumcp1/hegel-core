RELEASE_TYPE: patch

Hegel currently requires tests to be fully deterministic in their data generation, because Hypothesis does, but was not previously correctly reporting Hypothesis's flaky test errors back to the client (A test is flaky if it doesn't successfully replay - that is, when rerun with the same data generation, a different result is produced).
This release adds protocol support for reporting those flaky errors back.
