# Calibration evidence compatibility

Qualified target-before-stop cohort loading requires an explicit `calibration_contract`
inside the hashed calibration receipt. Receipt hashes establish content consistency;
they do not establish that return observations measured target-before-stop outcomes.

The current `fit_strategy_oof_calibration` producer emits:

```json
{"version": "strategy_return_v1", "outcome_source": "strategy_equity_curve"}
```

Its outcome is `positive_strategy_return_after_costs`, with chronological fit,
selection, and confirmation. It remains research-only for target-before-stop alerts.
Changing only its probability-definition string, even while recomputing its hash,
cannot make it compatible with event evidence.

The separately recognized event contract requires exactly these fields:

| Field | Required value or meaning |
| --- | --- |
| `version` | `target_stop_event_v1` |
| `outcome_source` | `resolved_target_stop_events` |
| `event_definition_hash` | 64 lowercase hex characters committing to target, protective stop, decision-relative horizon, entry/exit costs, and same-bar resolution policy |
| `outcome_rows_hash` | 64 lowercase hex characters committing to resolved target/stop event observations used for calibration |

The loader validates the structure, version, source, and hash formats. It also keeps
the existing receipt self-hash, permitted calibration-method, exact
`target_before_stop_after_costs` definition, numeric, cohort, and readiness checks.
Unknown fields, missing fields, malformed contracts, unsupported versions, and
incompatible sources fail closed.

There is no production event-calibration producer in this release. The event contract
is a structural boundary, not verification of target/stop paths or proof that an
estimate is calibrated. Positive test fixtures are explicitly synthetic structural
controls for this separate contract; they are not relabeled legacy return records.
Any future event producer must establish actual event provenance and validation.

Legacy calibration, strategy, ensemble, and readiness rows remain stored unchanged.
There is no migration, automatic stamping, backfill, deletion, or change to strategy
versions. Old qualification no longer carries across this boundary: a legacy receipt
cannot load a qualified cohort even when all its retained hashes and numerical gates
match. The ongoing frozen study and its historical evidence are unchanged.
