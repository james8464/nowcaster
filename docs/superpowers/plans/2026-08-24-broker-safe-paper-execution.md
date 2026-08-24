# Broker-Safe Paper Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add deterministic shadow and Alpaca paper-order execution with append-only audit evidence, idempotent recovery, and broker reconciliation.

**Architecture:** A new `src/trading` package isolates broker DTOs, clients, persistence, event parsing, reconciliation, and supervision from causal strategy research. The supervisor consumes already-authenticated `OrderIntent` provenance, persists before side effects, resolves ambiguous submissions by deterministic client order ID, and fails closed on unknown state.

**Tech Stack:** Python 3.11–3.13, Pydantic v2, SQLAlchemy/DuckDB, httpx, websockets, Typer, pytest/respx.

**Spec:** `docs/superpowers/specs/2026-08-24-broker-safe-trading-design.md`

## Global Constraints

- Live submission remains unavailable throughout this plan; Alpaca uses only `https://paper-api.alpaca.markets` and `wss://paper-api.alpaca.markets/stream`.
- Binance evidence cannot authorize an Alpaca order; exact provider/feed/symbol/interval/cohort identity is mandatory.
- Secrets never enter CLI arguments, logs, database rows, snapshots, fixtures, Git, exception text, or client-order IDs.
- Every persisted instant is explicit UTC and every quantity/price/value is finite and nonnegative where appropriate.
- Broker effects are preceded by a durable intent and followed by reconciliation; ambiguous effects are never blindly retried.
- CI uses controlled transports and complete official-shape fixtures; it never contacts Alpaca.

---

### Task 1: Trading configuration and immutable broker DTOs

**Files:**
- Create: `src/trading/__init__.py`
- Create: `src/trading/types.py`
- Modify: `src/config/settings.py`
- Create: `config/trading.yaml`
- Test: `tests/unit/test_trading_types.py`

**Interfaces:**
- Produces: `TradingEnvironment`, `BrokerOrderStatus`, `BrokerAccount`, `BrokerClock`, `BrokerAsset`, `BrokerOrderRequest`, `BrokerOrder`, `BrokerPosition`, `TradeUpdate`, `TradingConfig`.
- Consumes: existing `canonical_hash` and explicit UTC conventions.

- [ ] **Step 1: Write failing DTO tests** proving paper/live environment parsing, literal-Z UTC, Decimal-backed quantity/price normalization, non-finite rejection, order-status enumeration, and secret-free serialization.

```python
def test_order_request_requires_deterministic_client_identity_and_finite_positive_quantity() -> None:
    request = BrokerOrderRequest(
        client_order_id="nc-paper-abc123",
        symbol="AAPL",
        side="buy",
        quantity="1.25",
        order_type="limit",
        time_in_force="day",
        limit_price="190.50",
        extended_hours=False,
    )
    assert request.model_dump(mode="json")["quantity"] == "1.25"
    with pytest.raises(ValueError):
        request.model_copy(update={"quantity": "NaN"})
```

- [ ] **Step 2: Run the focused test and verify RED.**

Run: `.venv/bin/pytest -q tests/unit/test_trading_types.py`

Expected: import failure for `src.trading.types`.

- [ ] **Step 3: Implement the minimal frozen DTOs and configuration.** Use `Decimal(str(value))`, reject non-finite values, normalize symbols/IDs, forbid extras, and store only `paper_enabled`, `live_enabled=false`, reconciliation interval, stale-data threshold, and paper endpoint selection in `TradingConfig`.

```python
class BrokerOrderRequest(TradingModel):
    client_order_id: str = Field(min_length=8, max_length=48)
    symbol: str
    side: Literal["buy", "sell"]
    quantity: Decimal
    order_type: Literal["limit"]
    time_in_force: Literal["day", "gtc", "ioc"]
    limit_price: Decimal
    extended_hours: bool = False
```

- [ ] **Step 4: Run focused tests and Ruff.**

Run: `.venv/bin/pytest -q tests/unit/test_trading_types.py && .venv/bin/ruff check src/trading src/config/settings.py tests/unit/test_trading_types.py`

- [ ] **Step 5: Commit.**

```bash
git add src/trading src/config/settings.py config/trading.yaml tests/unit/test_trading_types.py
git commit -m "feat: define broker-safe trading contracts"
```

### Task 2: Schema v3 and append-only trading repository

**Files:**
- Modify: `src/database/schema.py`
- Modify: `src/database/engine.py`
- Create: `src/trading/repository.py`
- Test: `tests/integration/test_trading_repository.py`
- Modify: `tests/integration/test_strategy_schema.py`

**Interfaces:**
- Consumes: Task 1 DTOs.
- Produces: `TradingRepository.start_session`, `record_intent`, `record_submission`, `record_event`, `record_account_snapshot`, `record_position_snapshot`, `record_reconciliation`, `finish_session`, and read projections used by later tasks.

- [ ] **Step 1: Write failing migration/repository tests** that initialize an existing v2 database, apply v3 idempotently, persist a complete paper lifecycle, deduplicate the same event, reject a conflicting duplicate, and prove no table has a secret-bearing column.

```python
def test_trade_event_is_idempotent_but_conflicting_payload_fails(database: Database) -> None:
    repository = TradingRepository(database)
    event = _filled_trade_update(event_id="exec-1", filled_qty="1", price="190")
    assert repository.record_event(event) is True
    assert repository.record_event(event) is False
    with pytest.raises(ValueError, match="conflicting broker event"):
        repository.record_event(event.model_copy(update={"fill_price": Decimal("191")}))
```

- [ ] **Step 2: Run focused tests and verify RED** because schema v3/tables do not exist.

Run: `.venv/bin/pytest -q tests/integration/test_trading_repository.py tests/integration/test_strategy_schema.py`

- [ ] **Step 3: Add the v3 tables and natural keys** exactly as specified, with check constraints for finite-compatible numeric domains, explicit environment/status columns, canonical payload hashes, and no update path for immutable events/intents/risk decisions.

- [ ] **Step 4: Implement repository transactions.** Persist the intent and its source decision hash before submission; update only mutable broker-order projection fields while retaining every raw event append-only.

```python
def record_event(self, event: TradeUpdate) -> bool:
    event_hash = canonical_hash(event.model_dump(mode="json"))
    identity = event.event_id or canonical_hash((event.broker_order_id, event.event, event.broker_timestamp))
    return self._insert_once_or_verify("broker_order_events", identity, event_hash, event.model_dump(mode="python"))
```

- [ ] **Step 5: Run focused tests, schema compatibility, and Ruff.**

Run: `.venv/bin/pytest -q tests/integration/test_trading_repository.py tests/integration/test_strategy_schema.py && .venv/bin/ruff check src/database src/trading tests/integration/test_trading_repository.py`

- [ ] **Step 6: Commit.**

```bash
git add src/database src/trading/repository.py tests/integration/test_trading_repository.py tests/integration/test_strategy_schema.py
git commit -m "feat: persist append-only broker evidence"
```

### Task 3: Broker protocol and deterministic shadow broker

**Files:**
- Create: `src/trading/broker.py`
- Create: `src/trading/shadow.py`
- Create: `src/trading/idempotency.py`
- Test: `tests/unit/test_shadow_broker.py`

**Interfaces:**
- Produces: `BrokerClient` protocol, `ShadowBrokerClient`, and `client_order_id(intent, account_suffix, environment) -> str`.
- Consumes: Task 1 DTOs.

- [ ] **Step 1: Write failing protocol/identity tests** proving identical logical intents generate the same <=48-character ID, any material field changes it, secrets/account IDs are not embedded, shadow submission returns an acknowledged non-fill, and cancellation/list calls are deterministic.

```python
def test_client_order_id_is_stable_and_contains_no_account_or_strategy_text() -> None:
    first = client_order_id(_intent(), account_suffix="1234", environment=TradingEnvironment.PAPER)
    second = client_order_id(_intent(), account_suffix="1234", environment=TradingEnvironment.PAPER)
    assert first == second
    assert len(first) <= 48
    assert "1234" not in first and "rsi" not in first
```

- [ ] **Step 2: Run and verify RED.**

Run: `.venv/bin/pytest -q tests/unit/test_shadow_broker.py`

- [ ] **Step 3: Implement the protocol and hash identity.** Use a versioned canonical SHA-256 payload and a fixed `nc1p-`/`nc1s-` prefix; never use Python `hash()` or random UUIDs.

- [ ] **Step 4: Implement `ShadowBrokerClient`** with injected account/clock/assets/positions and an in-memory order projection that never creates a fill.

- [ ] **Step 5: Run focused tests and commit.**

```bash
.venv/bin/pytest -q tests/unit/test_shadow_broker.py
git add src/trading/broker.py src/trading/shadow.py src/trading/idempotency.py tests/unit/test_shadow_broker.py
git commit -m "feat: add deterministic shadow broker"
```

### Task 4: Alpaca paper REST adapter

**Files:**
- Create: `src/trading/alpaca.py`
- Create: `tests/fixtures/trading/alpaca_account.json`
- Create: `tests/fixtures/trading/alpaca_clock.json`
- Create: `tests/fixtures/trading/alpaca_asset.json`
- Create: `tests/fixtures/trading/alpaca_order.json`
- Create: `tests/fixtures/trading/alpaca_positions.json`
- Test: `tests/unit/test_alpaca_trading.py`
- Modify: `tests/unit/test_secret_scan.py`

**Interfaces:**
- Produces: `AlpacaTradingClient(credentials, client, clock)` implementing `BrokerClient` for the fixed paper URL.
- Consumes: Task 1 DTOs and Task 3 protocol.

- [ ] **Step 1: Write complete-shape fixture tests** for account, clock, asset, list/get/submit/cancel orders, positions, HTTP 403/422 rejection, 429 retry-after handling, timeouts, malformed responses, and diagnostic redaction.

```python
def test_submit_uses_paper_endpoint_headers_and_exact_limit_payload(respx_mock) -> None:
    route = respx_mock.post("https://paper-api.alpaca.markets/v2/orders").mock(
        return_value=httpx.Response(200, json=_fixture("alpaca_order.json"))
    )
    order = _client().submit_order(_request())
    assert order.client_order_id == "nc1p-abc"
    assert route.calls[0].request.headers["APCA-API-KEY-ID"] == "paper-key"
    assert route.calls[0].request.json()["type"] == "limit"
```

- [ ] **Step 2: Run and verify RED.**

Run: `.venv/bin/pytest -q tests/unit/test_alpaca_trading.py`

- [ ] **Step 3: Implement strict paper endpoint selection and parsers.** Decimal values remain strings at the HTTP boundary. Exceptions expose status, request ID, and bounded broker message but redact credential values and authorization headers.

- [ ] **Step 4: Implement bounded retry rules.** Retry GET and rate-limited/transient requests with injected sleep; never retry POST `/orders` inside the adapter after an ambiguous transport failure.

- [ ] **Step 5: Run adapter and secret tests, then commit.**

```bash
.venv/bin/pytest -q tests/unit/test_alpaca_trading.py tests/unit/test_secret_scan.py
git add src/trading/alpaca.py tests/fixtures/trading tests/unit/test_alpaca_trading.py tests/unit/test_secret_scan.py
git commit -m "feat: connect Alpaca paper trading REST"
```

### Task 5: Trade-update stream parser and reconnect transport

**Files:**
- Modify: `pyproject.toml`
- Create: `src/trading/stream.py`
- Create: `tests/fixtures/trading/alpaca_trade_updates.json`
- Test: `tests/unit/test_trade_update_stream.py`

**Interfaces:**
- Produces: `parse_trade_update(message: bytes | str) -> TradeUpdate | StreamControl`, `AlpacaTradeUpdateStream.iter_updates(stop)`, and `StreamControl` authorization/listening state.
- Consumes: Task 1 DTOs.

- [ ] **Step 1: Write failing parser tests** for authorization, listening acknowledgement, new, partial fill, fill, canceled, expired, rejected, replaced, suspended, calculated, cancel rejection, unknown event, binary paper frames, malformed JSON, and oversized messages.

```python
@pytest.mark.parametrize("event", ["new", "partial_fill", "fill", "canceled", "rejected", "suspended"])
def test_documented_trade_update_round_trips(event: str) -> None:
    update = parse_trade_update(_fixture_event(event))
    assert isinstance(update, TradeUpdate)
    assert update.event == event
```

- [ ] **Step 2: Run and verify RED.**

Run: `.venv/bin/pytest -q tests/unit/test_trade_update_stream.py`

- [ ] **Step 3: Add `websockets>=15,<17` and implement pure parsing first.** Unknown events are valid persisted updates with `known_event=False`; malformed/oversized frames raise typed protocol errors without including secrets.

- [ ] **Step 4: Implement reconnect transport** with injected connector/backoff, explicit authentication then listen acknowledgement, heartbeat receipt timestamps, stop token, and no unbounded task/thread creation.

- [ ] **Step 5: Run tests and commit.**

```bash
.venv/bin/pytest -q tests/unit/test_trade_update_stream.py
git add pyproject.toml src/trading/stream.py tests/fixtures/trading/alpaca_trade_updates.json tests/unit/test_trade_update_stream.py
git commit -m "feat: consume Alpaca trade updates safely"
```

### Task 6: Reconciliation and crash-safe supervisor

**Files:**
- Create: `src/trading/reconciliation.py`
- Create: `src/trading/supervisor.py`
- Test: `tests/integration/test_trading_supervisor.py`

**Interfaces:**
- Produces: `ReconciliationResult`, `TradingSupervisor.start`, `submit_intent`, `consume_update`, `reconcile`, `freeze`, and `shutdown`.
- Consumes: Tasks 2–5.

- [ ] **Step 1: Write failing end-to-end tests** for startup reconciliation before admission, normal submission, partial/final fill projection, duplicate/reordered updates, restart with open order, broker/local position mismatch, unknown event freeze, reconnect reconciliation, clean shutdown, and ambiguous POST lookup-before-retry.

```python
def test_ambiguous_submission_queries_client_id_before_retrying(database: Database) -> None:
    broker = AmbiguousThenExistingBroker(existing=_broker_order())
    outcome = _supervisor(database, broker).submit_intent(_intent())
    assert outcome.status == "accepted"
    assert broker.submit_calls == 1
    assert broker.lookup_client_ids == [outcome.client_order_id]
```

- [ ] **Step 2: Run and verify RED.**

Run: `.venv/bin/pytest -q tests/integration/test_trading_supervisor.py`

- [ ] **Step 3: Implement reconciliation as a pure comparison** of local order/position projections against broker truth. Persist the comparison and freeze on unresolved missing, extra, quantity, status, or account mismatch.

- [ ] **Step 4: Implement the supervisor state machine.** It must persist intent before submission, reconcile before the first post-start order, serialize per-account effects, and look up deterministic client ID after ambiguous POST before deciding whether a retry is safe.

- [ ] **Step 5: Run focused and existing execution tests.**

Run: `.venv/bin/pytest -q tests/integration/test_trading_supervisor.py tests/unit/test_execution_engine.py tests/integration/test_strategy_engine.py`

- [ ] **Step 6: Commit.**

```bash
git add src/trading/reconciliation.py src/trading/supervisor.py tests/integration/test_trading_supervisor.py
git commit -m "feat: reconcile paper broker state"
```

### Task 7: Trading CLI and credential-safe session lifecycle

**Files:**
- Modify: `src/cli.py`
- Create: `src/trading/service.py`
- Modify: `.env.example`
- Test: `tests/integration/test_trading_cli.py`
- Modify: `tests/integration/test_strategy_cli.py`

**Interfaces:**
- Produces CLI namespace `trading shadow`, `trading paper`, `trading status`, `trading reconcile`, `trading freeze`, and `trading stop`.
- Consumes: Tasks 1–6.

- [ ] **Step 1: Write failing CliRunner tests** proving paper refuses missing credentials, rejects a live environment/base URL, never echoes secrets, performs reconciliation before reporting ready, emits bounded JSON-line progress, and records terminal session state on cancellation/error.

```python
def test_paper_command_never_accepts_live_endpoint_or_echoes_secret(monkeypatch) -> None:
    monkeypatch.setenv("APCA_API_KEY_ID", "visible-key")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "never-print-this")
    result = runner.invoke(app, ["trading", "paper", "--base-url", "https://api.alpaca.markets"])
    assert result.exit_code != 0
    assert "never-print-this" not in result.output
```

- [ ] **Step 2: Run and verify RED.**

Run: `.venv/bin/pytest -q tests/integration/test_trading_cli.py`

- [ ] **Step 3: Implement service construction and CLI commands.** Credentials come only from environment aliases, are validated as a pair, and are handed directly to the adapter. `trading paper` has no `--live`, arbitrary endpoint, key, or secret option.

- [ ] **Step 4: Run CLI suites and secret scan.**

Run: `.venv/bin/pytest -q tests/integration/test_trading_cli.py tests/integration/test_strategy_cli.py tests/unit/test_secret_scan.py && .venv/bin/python scripts/scan_tracked_secrets.py`

- [ ] **Step 5: Commit.**

```bash
git add src/cli.py src/trading/service.py .env.example tests/integration/test_trading_cli.py tests/integration/test_strategy_cli.py
git commit -m "feat: operate shadow and paper sessions"
```

### Task 8: Paper execution documentation and plan-wide verification

**Files:**
- Modify: `README.md`
- Modify: `docs/data-providers.md`
- Create: `docs/paper-trading-operations.md`
- Modify: `docs/privacy.md`
- Modify: `Makefile`

**Interfaces:**
- Produces beginner operations guide and `make verify-paper-trading`.
- Consumes: all previous tasks.

- [ ] **Step 1: Document setup and honest limitations.** Explain separate paper keys, exact commands, shadow-first workflow, paper reset consequences, reconciliation/freeze semantics, cache/data venue identity, and Alpaca's documented simulation omissions.

- [ ] **Step 2: Add `verify-paper-trading`** invoking trading DTO, repository, adapter, stream, supervisor, and CLI suites plus the secret scan.

- [ ] **Step 3: Run the complete Python suite and quality gates.**

Run: `.venv/bin/pytest -q`

Run: `.venv/bin/ruff format --check . && .venv/bin/ruff check . && git diff --check`

Run: `make verify-paper-trading secret-scan`

- [ ] **Step 4: Confirm no live endpoint is reachable from CLI.** Run CLI help and negative invocation tests; inspect tracked files for `api.alpaca.markets` and allow it only in documentation/tests that prove rejection.

- [ ] **Step 5: Commit.**

```bash
git add README.md docs Makefile
git commit -m "docs: explain broker-safe paper operation"
```
