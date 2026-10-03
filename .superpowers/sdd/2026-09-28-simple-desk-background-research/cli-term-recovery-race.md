# CLI termination recovery verification

The test-only recovery fixture now stops at exactly one durable reservation, leaving three unreserved attempts for an authenticated fresh execution. Normal, low-core, and coverage-focused checks passed. No Python production code, runtime identity, evaluation rules, deadline, or packaged binary changed.

## Failure evidence and confidence

CI run 37123233207 retained 1769 passed, 7 skipped, and one failed recovery test. The resumed worker never met the test's `waiting` predicate within the unchanged 90 seconds. CI did not retain the worker events or final registry state, so the precise CI terminal state is unproven.

A local isolated reproduction proved a fixture race: polling for any attempt can observe all four already reserved. SIGTERM then retains all four as interrupted; authenticated resume has no unreserved budget or evaluable candidate and legitimately reaches `failed`, not `waiting`. That reproduction reached terminal failure in 3.10 seconds. This establishes the flawed test precondition with high confidence; attribution of the CI failure to that exact terminal state remains an inference. Coverage-specific worker slowdown was not established.

## Changes and retained guarantees

Only `tests/integration/test_background_research_cli.py` changed, with 85 insertions and 3 deletions. A temporary entry wrapper calls the real registry append first, then holds the first durable reservation until the parent's actual SIGTERM persists STOP. The gate has a 45-second self-timeout; normal CLI resume still uses the real evaluator. The test asserts exactly one initial attempt, exit zero, durable STOP, the same batch and four-attempt budget, immutable prefix, a newly completed result, and unchanged old control and training databases.

The event helper now reports unmatched failed or blocked states immediately with status and recent events. Its predicate is checked first, preserving callers that intentionally accept those states. Timeout diagnostics include return code, status, and recent events. A regression deliberately interrupts all four reservations and verifies terminal failure is reported instead of waiting for an impossible state. Existing recovery 90-second and helper 45-second bounds remain unchanged.

## Verification

- RED against the old helper: 1 failed in 20.89 seconds, expected terminal failure but received the old timeout message. This initial diagnostic used 15 seconds; the final regression uses the existing 45-second default to avoid a cold-start bound change.
- Focused normal: 2 passed in 36.47 seconds.
- Focused low-core with one resumed worker: 2 passed in 38.16 seconds.
- Focused coverage with the CI coverage flags: 2 passed in 38.87 seconds.
- Relevant complete file using its default intermediate artifact: 16 passed, 1 failed, 1 skipped in 260.72 seconds. This failure remains retained, not repaired or erased.
- Signed packaged child cleanup and crash recovery case: 1 passed in 48.35 seconds.
- Signed packaged trainer preparation STOP case, previously skipped: 1 passed in 17.57 seconds.

Logs are `/tmp/NowcasterCLI-Exhausted-20261003-RED.log`, `/tmp/NowcasterCLI-Recovery-20261003-{GREEN,LowCore,Coverage,File}.log`, `/tmp/NowcasterCLI-PackagedVerified-20261003-Run.log`, and `/tmp/NowcasterCLI-PackagedPreparation-20261003.log`. Each verification retained its isolated temporary fixture. Formatting, lint, and diff checks passed.

## Packaged artifact limits

The complete-file failure was the default packaged child-detection case: no compute children appeared within 45 seconds after ownership. Its retained registry remained idle with zero attempts and no batch; the original test did not preserve later stdout or stderr, so its actual failure reason cannot be recovered. The January source endpoint closes the registered window and is before the current clock; no future-data cause was established.

Both isolated packaged checks explicitly used `/tmp/NowcasterFinalRelease-20261003/PackagedAcceptance/nowcaster-engine`, SHA256 `663f0f52c510e22fc343559737f8230f435ebeb70da881e87bce77c8e5a9063d`, matching its adjacent manifest. The default intermediate fingerprint differs, but both manifests identify the same source tree. This is not proof of an unsigned-artifact defect or a source-age defect. The verified child cleanup case observed real compute children, killed only the authenticated group, preserved attempt prefix and interrupted receipts, left an unrelated child intact, resumed to an explicitly accepted failed terminal state, and stopped with exit zero and empty stderr.

The scoped source is frozen and reviewed by the parent. No full Python suite or CI rerun was performed after this fixture repair; CI remains a required root-owned release gate.
