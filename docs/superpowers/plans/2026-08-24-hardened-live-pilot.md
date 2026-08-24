# Hardened Live Pilot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Keychain-backed credentials, a signed-engine identity, expiring manual arming, hard live-pilot caps, and a production release gate while keeping live submission locked without external evidence.

**Architecture:** Paper and live share broker DTOs but use distinct fixed endpoints, Keychain services, environment labels, and credentials. A pure live-lock evaluator requires a valid readiness receipt, signed bundled-engine identity, account match, production signature posture, and short-lived manual arm before constructing a live client. Release automation refuses a live-capable artifact unless Developer ID signing, hardened runtime, notarization, stapling, assessment, SBOM, and all tests pass.

**Tech Stack:** Python/Pydantic/httpx, Swift 6/SwiftUI/Security.framework/CryptoKit, macOS codesign/notarytool/stapler/spctl, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-08-24-broker-safe-trading-design.md`

## Global Constraints

- `live_enabled` defaults false and cannot be enabled by a CLI flag alone.
- Ad-hoc/development builds, arbitrary Python executables, expired receipts/arms, mismatched accounts, missing Keychain items, or missing external evidence remain `Live Locked`.
- Paper and live endpoints and credentials are distinct; fallback from live to paper or paper to live is forbidden.
- First-release live ceilings are immutable: position `min($100, 0.10% equity)`, gross `min($500, 0.50%)`, daily loss `min($25, 0.05%)`, 30-minute arm, no extended-hours entry, no overnight equity position.
- Configuration can only tighten those ceilings.
- Live entries are price-collared marketable limit orders. Emergency closes remain separately confirmed and reconciled.
- No test or CI job submits an order to a real broker endpoint.

---

### Task 1: macOS Keychain credential vault

**Files:**
- Create: `macos/Nowcaster/Sources/NowcasterApp/Security/BrokerCredentialVault.swift`
- Modify: `macos/Nowcaster/Sources/NowcasterApp/SettingsView.swift`
- Create: `macos/Nowcaster/Sources/NowcasterApp/Features/Settings/BrokerCredentialsView.swift`
- Create: `macos/Nowcaster/Tests/NowcasterAppTests/BrokerCredentialVaultTests.swift`
- Modify: `macos/Nowcaster/Tests/NowcasterAppTests/SettingsTests.swift`

**Interfaces:**
- Produces: `BrokerCredentialVault.save`, `status`, `loadForSession`, `delete`; separate `paper` and `live` Keychain services.
- Consumes: Security.framework generic-password APIs.

- [ ] **Step 1: Write failing vault tests** with an injected Keychain client proving create/replace/delete, paper/live separation, access-after-first-unlock policy, no UserDefaults storage, no readable secret presentation, and bounded OSStatus errors.

```swift
@Test func paperAndLiveCredentialsUseDistinctServices() throws {
    let store = RecordingKeychainClient()
    let vault = BrokerCredentialVault(client: store)
    try vault.save(.init(keyID: "paper", secret: "p"), environment: .paper)
    try vault.save(.init(keyID: "live", secret: "l"), environment: .live)
    #expect(store.services == ["com.james8464.nowcaster.alpaca.paper", "com.james8464.nowcaster.alpaca.live"])
}
```

- [ ] **Step 2: Run and verify RED.**

Run: `swift test --package-path macos/Nowcaster --filter BrokerCredentialVaultTests`

- [ ] **Step 3: Implement the Security.framework adapter.** Store key ID as account and JSON-encoded secret material as value data; use app-only service names, `kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly`, and update-or-add semantics.

- [ ] **Step 4: Implement settings UI** with SecureFields and explicit Save/Replace/Delete/Test actions. Show only configured/not-configured and account suffix after a successful broker test.

- [ ] **Step 5: Run Swift tests and commit.**

```bash
swift test --package-path macos/Nowcaster
git add macos/Nowcaster
git commit -m "feat: store broker credentials in Keychain"
```

### Task 2: Secret-safe child process environment

**Files:**
- Modify: `macos/Nowcaster/Sources/NowcasterApp/Services/EngineRunner.swift`
- Modify: `macos/Nowcaster/Sources/NowcasterApp/Models/EngineJob.swift`
- Modify: `macos/Nowcaster/Sources/NowcasterApp/AppModel.swift`
- Test: `macos/Nowcaster/Tests/NowcasterAppTests/EngineRunnerTests.swift`
- Test: `macos/Nowcaster/Tests/NowcasterAppTests/AppModelTests.swift`

**Interfaces:**
- Produces: session-only `EngineSecretEnvironment` injected into `Process.environment`, with redacted diagnostics and zero persisted copy.
- Consumes: Task 1 vault.

- [ ] **Step 1: Write failing tests** proving credentials are present in the launched process environment but absent from arguments, durable job records, diagnostic buffers, snapshots, UserDefaults, errors, and cancellation output.

- [ ] **Step 2: Run and verify RED.**

Run: `swift test --package-path macos/Nowcaster --filter 'EngineRunnerTests|AppModelTests'`

- [ ] **Step 3: Implement explicit environment injection and redaction.** Build the child environment from the current process plus exact paper/live aliases; scrub exact values and authorization-header forms from every captured line before storage/display.

- [ ] **Step 4: Clear in-memory credential values after process launch/termination** and prohibit encoding or Equatable debug descriptions that expose them.

- [ ] **Step 5: Run tests and commit.**

```bash
swift test --package-path macos/Nowcaster
git add macos/Nowcaster/Sources/NowcasterApp macos/Nowcaster/Tests/NowcasterAppTests
git commit -m "feat: inject broker secrets without persistence"
```

### Task 3: Signed bundled-engine identity

**Files:**
- Create: `scripts/build_engine_bundle.sh`
- Create: `scripts/engine_manifest.py`
- Create: `scripts/engine_entry.py`
- Modify: `pyproject.toml`
- Modify: `scripts/build_macos_app.sh`
- Modify: `macos/Nowcaster/Sources/NowcasterApp/Models/EngineJob.swift`
- Create: `macos/Nowcaster/Sources/NowcasterApp/Security/EngineIdentity.swift`
- Test: `tests/unit/test_engine_manifest.py`
- Test: `macos/Nowcaster/Tests/NowcasterAppTests/EngineRunnerTests.swift`
- Modify: `Makefile`

**Interfaces:**
- Produces: `Nowcaster.app/Contents/Helpers/nowcaster-engine`, signed manifest with code/config/module hashes, and `EngineIdentity.verifyLiveEligible`.
- Consumes: existing Python entry point and app build.

- [ ] **Step 1: Write failing Python manifest tests** proving deterministic file hashing, excluded caches/secrets, mutation detection, and strict schema.

- [ ] **Step 2: Write failing Swift identity tests** proving research may use an external runtime while live requires the exact bundled helper, manifest match, bundle location, production signature posture, and no quarantine/mutable-path substitution.

- [ ] **Step 3: Run and verify RED.**

Run: `.venv/bin/pytest -q tests/unit/test_engine_manifest.py && swift test --package-path macos/Nowcaster --filter EngineRunnerTests`

- [ ] **Step 4: Implement reproducible engine bundling.** Add PyInstaller to the development/release tool set, make `engine_entry.py` call `src.cli.app`, and run `python -m PyInstaller --clean --onefile --name nowcaster-engine scripts/engine_entry.py` with an explicit spec/build/dist directory under `build/engine`. Generate a canonical manifest, place the executable and manifest under `Contents/Helpers`, and sign nested binaries before signing the app.

- [ ] **Step 5: Implement native verification** using bundle-relative URL, manifest hash, executable permissions, and Security.framework code-signing inspection. No external path can return live-eligible.

- [ ] **Step 6: Run bundle/build tests and commit.**

```bash
.venv/bin/pytest -q tests/unit/test_engine_manifest.py
swift test --package-path macos/Nowcaster
make engine-bundle macos-app
git add scripts Makefile pyproject.toml macos/Nowcaster
git commit -m "feat: bundle a verifiable trading engine"
```

### Task 4: Live endpoint adapter and immutable pilot caps

**Files:**
- Modify: `src/trading/alpaca.py`
- Create: `src/trading/live.py`
- Modify: `src/trading/risk.py`
- Test: `tests/unit/test_live_broker_lock.py`
- Modify: `tests/unit/test_alpaca_trading.py`
- Modify: `tests/unit/test_trading_risk.py`

**Interfaces:**
- Produces: `LivePilotPolicy`, `LiveBrokerFactory.create(context)`, and fixed live endpoint adapter available only after lock evaluation.
- Consumes: readiness receipt, engine identity receipt, build posture, account state, and policy.

- [ ] **Step 1: Write failing lock tests** for each missing/expired/mismatched receipt, engine identity, production signature, account suffix, environment, credential label, arm, reconciliation, health, and pilot-cap condition.

```python
def test_live_factory_never_constructs_client_for_ad_hoc_build() -> None:
    with pytest.raises(LiveLockedError, match="production_signature_required"):
        LiveBrokerFactory().create(_context(signature_posture="adhoc"))
```

- [ ] **Step 2: Write exact cap-boundary tests** for $100/0.10%, $500/0.50%, $25/0.05%, 30 minutes, extended hours, overnight equities, entry order type, and shortable/easy-to-borrow.

- [ ] **Step 3: Run and verify RED.**

Run: `.venv/bin/pytest -q tests/unit/test_live_broker_lock.py tests/unit/test_alpaca_trading.py tests/unit/test_trading_risk.py`

- [ ] **Step 4: Refactor Alpaca adapter endpoint selection into an internal enum.** Public constructors accept an environment, not a URL; paper/live host constants cannot be overridden.

- [ ] **Step 5: Implement live factory and hard ceilings.** Evaluate all locks before allocating a client. Configuration parsing rejects any value above a ceiling instead of clamping silently.

- [ ] **Step 6: Run tests and commit.**

```bash
.venv/bin/pytest -q tests/unit/test_live_broker_lock.py tests/unit/test_alpaca_trading.py tests/unit/test_trading_risk.py
git add src/trading tests/unit/test_live_broker_lock.py tests/unit/test_alpaca_trading.py tests/unit/test_trading_risk.py
git commit -m "feat: lock live broker behind pilot controls"
```

### Task 5: Expiring manual arm and native arming ceremony

**Files:**
- Create: `src/trading/arming.py`
- Modify: `src/trading/repository.py`
- Modify: `src/cli.py`
- Create: `macos/Nowcaster/Sources/NowcasterApp/Features/ExecutionCenter/LiveArmingView.swift`
- Modify: `macos/Nowcaster/Sources/NowcasterApp/Features/ExecutionCenter/ExecutionCenterView.swift`
- Modify: `macos/Nowcaster/Sources/NowcasterApp/Models/EngineJob.swift`
- Test: `tests/unit/test_live_arming.py`
- Test: `tests/integration/test_trading_cli.py`
- Modify: `macos/Nowcaster/Tests/NowcasterAppTests/ExecutionCenterTests.swift`

**Interfaces:**
- Produces: `ArmRequest`, `TradingArm`, `ArmingService.arm/disarm/current`; native sheet supplying exact account suffix and loss acknowledgement.
- Consumes: Task 4 live lock and readiness receipt.

- [ ] **Step 1: Write failing Python tests** for exact suffix/phrase, receipt hash, account/environment match, explicit limits, 30-minute maximum, actual-clock expiry, restart invalidation, disarm, breaker invalidation, and no secret/token in snapshot.

- [ ] **Step 2: Write failing Swift tests** for disabled arm when any gate fails, exact suffix, explicit maximum-loss display, destructive visual role, default Cancel focus, expiry countdown, and no persistence across app restart.

- [ ] **Step 3: Run and verify RED.**

Run: `.venv/bin/pytest -q tests/unit/test_live_arming.py tests/integration/test_trading_cli.py`

Run: `swift test --package-path macos/Nowcaster --filter ExecutionCenterTests`

- [ ] **Step 4: Implement append-only arm lifecycle.** Arm identity is hashed and stored without credentials. The active arm expires on the earliest of 30 minutes, readiness expiry, process/session end, disarm, or breaker.

- [ ] **Step 5: Implement native ceremony and typed invocation.** The live-start engine job can be built only from an in-memory successful arming result and bundled-engine configuration.

- [ ] **Step 6: Run tests and commit.**

```bash
.venv/bin/pytest -q tests/unit/test_live_arming.py tests/integration/test_trading_cli.py
swift test --package-path macos/Nowcaster
git add src/trading src/cli.py tests macos/Nowcaster
git commit -m "feat: require expiring manual live arm"
```

### Task 6: Production signing, notarization, assessment, and SBOM gate

**Files:**
- Modify: `.github/workflows/release.yml`
- Create: `scripts/verify_production_release.sh`
- Create: `scripts/generate_sbom.py`
- Modify: `scripts/build_macos_app.sh`
- Modify: `Makefile`
- Test: `tests/unit/test_release_gate.py`
- Test: `tests/unit/test_sbom.py`

**Interfaces:**
- Produces: strict production release verifier and bounded CycloneDX-compatible JSON SBOM.
- Consumes: signed app/helper, notary result, stapled ticket, archive, dependency metadata.

- [ ] **Step 1: Write failing release-script tests** using controlled fake command executables to prove missing identity/secrets, ad-hoc signature, unsigned nested helper, missing hardened runtime/timestamp, notarization rejection, missing staple, failed `spctl`, and checksum mismatch all fail.

- [ ] **Step 2: Write failing SBOM tests** proving deterministic sorted Python/Swift component identity, hashes, licenses when available, no environment values, and archive inclusion.

- [ ] **Step 3: Run and verify RED.**

Run: `.venv/bin/pytest -q tests/unit/test_release_gate.py tests/unit/test_sbom.py`

- [ ] **Step 4: Make tagged release credentials mandatory.** Remove the conditional fallback for production tags; import Developer ID, sign nested helpers then app with hardened runtime/timestamp, submit with `notarytool --wait`, staple, and validate.

- [ ] **Step 5: Add final checks** for `codesign --verify --deep --strict`, signature details, `spctl --assess --type execute`, `stapler validate`, SBOM schema/hash, archive checksum, and no `get-task-allow` entitlement.

- [ ] **Step 6: Run controlled tests and local ad-hoc negative verification; commit.**

```bash
.venv/bin/pytest -q tests/unit/test_release_gate.py tests/unit/test_sbom.py
make macos-app
! ./scripts/verify_production_release.sh build/Nowcaster.app
git add .github/workflows/release.yml scripts Makefile tests/unit/test_release_gate.py tests/unit/test_sbom.py
git commit -m "build: require notarized live-capable releases"
```

### Task 7: Security/privacy documentation and final live-lock audit

**Files:**
- Modify: `README.md`
- Modify: `docs/privacy.md`
- Modify: `docs/live-readiness.md`
- Create: `docs/live-pilot-operations.md`
- Modify: `docs/native_verification.md`
- Modify: `.env.example`
- Modify: `scripts/scan_tracked_secrets.py`
- Test: `tests/unit/test_secret_scan.py`

**Interfaces:**
- Produces operator runbook and enhanced scanning for Keychain service names, credential aliases, broker headers, endpoints, and fixture exceptions.
- Consumes: all prior tasks.

- [ ] **Step 1: Extend secret-scan RED tests** with committed-file and reachable-history examples for Alpaca keys/secrets, authorization payloads, Keychain dumps, account IDs, and accidentally captured environment diagnostics.

- [ ] **Step 2: Run and verify RED, then implement bounded detectors** that preserve documented placeholders and official fixtures without allowing value-shaped secrets.

- [ ] **Step 3: Document exact operational procedure** for paper evidence, readiness, Keychain replacement/deletion, arming, freeze, flatten, incident response, broker dashboard verification, signed-release verification, and the prohibition on interpreting pilot evidence as unrestricted readiness.

- [ ] **Step 4: Run secret scan and commit.**

```bash
.venv/bin/pytest -q tests/unit/test_secret_scan.py
.venv/bin/python scripts/scan_tracked_secrets.py
git add README.md docs .env.example scripts/scan_tracked_secrets.py tests/unit/test_secret_scan.py
git commit -m "docs: define live pilot security operations"
```

### Task 8: Whole-product verification and publication

**Files:**
- Modify only if verification exposes a tested defect; otherwise no product file changes.

**Interfaces:**
- Produces review-clean, pushed release candidate with `Live Locked` in the absence of external conditions.
- Consumes: all three implementation plans.

- [ ] **Step 1: Run full Python verification.**

Run: `.venv/bin/pytest -q`

Run: `.venv/bin/ruff format --check . && .venv/bin/ruff check . && git diff --check`

- [ ] **Step 2: Run complete Swift/release verification.**

Run: `swift test --package-path macos/Nowcaster`

Run: `swift build -c release --package-path macos/Nowcaster`

Run: `make verify-research-fixtures verify-swift-fixture-parity verify-paper-trading verify-trading-readiness secret-scan macos-app macos-screenshots macos-ui-test release-archive`

- [ ] **Step 3: Verify local artifact posture.** Confirm ad-hoc local app remains `Live Locked`, checksum validates, and production verification intentionally fails without Apple credentials/notary ticket.

- [ ] **Step 4: Review the whole branch** for causal, broker-side-effect, reconciliation, risk, credential, snapshot, UI, signing, and documentation defects. Fix only reproduced findings through red/green tests and repeat verification.

- [ ] **Step 5: Merge and push only after the reviewed candidate and merged tree are green.** Verify remote SHA equals local SHA. Preserve external caches/credentials and remove only the owned temporary worktree/build database.
