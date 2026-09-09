"""Registered replay safety and real tiny ZIP/engine integration (never history)."""

import hashlib
import io
import json
import os
import zipfile
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import httpx
import pandas as pd
import pytest

from scripts import run_historical_replay as runner
from src.research.historical_replay_reporting import period_changes, render_report
from src.research.prospective_runtime import load_registry
from src.strategies.types import canonical_hash

ROOT = Path(__file__).resolve().parents[1]


def fixture_discovery(cache):
    retained = json.loads((ROOT / runner.DISCOVERY_PATH).read_text())
    discovery = deepcopy(retained)
    discovery.update(start="2025-01-01T00:00:00+00:00", end_exclusive="2025-01-02T00:00:00+00:00")
    discovery["archive_manifests"] = []
    payloads = {}
    for symbol in ("BTCUSDT", "ETHUSDT"):
        name = f"{symbol}-1h-2025-01-01.zip"
        buffer = io.BytesIO()
        lines = []
        for hour in (0, 1, 3):
            stamp = int((pd.Timestamp("2025-01-01T00:00:00Z") + pd.Timedelta(hours=hour)).timestamp() * 1000)
            lines.append(f"{stamp},100,101,99,100,1000,{stamp + 3599999},100000,10,500,50000,0")
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(name.removesuffix(".zip") + ".csv", "\n".join(lines))
        payload = buffer.getvalue()
        digest = hashlib.sha256(payload).hexdigest()
        item = dict(
            symbol=symbol,
            name=name,
            frequency="daily",
            sha256=digest,
            compressed_bytes=len(payload),
            selected_rows=3,
            invalid_boundary_rows=0,
            checksum_verified=True,
        )
        discovery["archive_manifests"].append(item)
        parent = cache / "binance-public-data/spot/daily/klines" / symbol / "1h"
        parent.mkdir(parents=True)
        (parent / name).write_bytes(payload)
        checksum = f"{digest}  {name}\n".encode()
        (parent / f"{name}.CHECKSUM").write_bytes(checksum)
        payloads[name] = payload
        payloads[f"{name}.CHECKSUM"] = checksum
    discovery["archive_manifest_hash"] = canonical_hash(discovery["archive_manifests"])
    return discovery, payloads


def test_production_discovery_is_pinned_and_checks_definitions(tmp_path):
    data = runner.load_pinned_discovery(ROOT)
    assert len(data["trials"]) == 24
    assert len(data["archive_manifests"]) == 260
    path = tmp_path / runner.DISCOVERY_PATH
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="discovery.*digest"):
        runner.load_pinned_discovery(tmp_path)
    bad = deepcopy(data)
    bad["candidates"][0]["strategy_definition_hash"] = "0" * 64
    with pytest.raises(ValueError, match="definition"):
        runner.validate_candidates(bad, load_registry(ROOT))


@pytest.mark.parametrize(
    "changes",
    [
        {"name": "../../escape.zip"},
        {"frequency": "../daily"},
        {"symbol": "../BTCUSDT"},
        {"compressed_bytes": True},
        {"selected_rows": -1},
        {"checksum_verified": False},
        {"sha256": "x" * 64},
        {"surprise": 1},
    ],
)
def test_manifest_rejects_malformed_fields_before_io(tmp_path, changes):
    data, _ = fixture_discovery(tmp_path / "cache")
    data["archive_manifests"][0].update(changes)
    data["archive_manifest_hash"] = canonical_hash(data["archive_manifests"])
    with pytest.raises(ValueError, match="manifest"):
        runner.load_scopes(data, tmp_path / "absent")
    assert not (tmp_path / "absent").exists()


def test_offline_loader_retains_gaps_and_rejects_missing_or_changed_files(tmp_path):
    cache = tmp_path / "cache"
    data, _ = fixture_discovery(cache)
    scopes = runner.load_scopes(data, cache)
    assert [len(scopes[s]) for s in ("BTCUSDT", "ETHUSDT")] == [3, 3]
    path = next(cache.rglob("*.zip"))
    old = path.read_bytes()
    path.write_bytes(old + b"changed")
    with pytest.raises(ValueError, match="pinned archive"):
        runner.load_scopes(data, cache, allow_download=True)
    path.unlink()
    with pytest.raises(FileNotFoundError, match="offline"):
        runner.load_scopes(data, cache)


def test_download_uses_exact_official_files_and_rejects_changed_checksum(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    data, payloads = fixture_discovery(cache)
    for path in cache.rglob("*.zip*"):
        path.unlink()
    client = httpx.Client

    def serve(request):
        assert str(request.url).startswith("https://data.binance.vision/data/spot/daily/klines/")
        return httpx.Response(200, content=payloads[request.url.path.rsplit("/", 1)[1]])

    monkeypatch.setattr(
        runner.httpx, "Client", lambda **kwargs: client(**({"transport": httpx.MockTransport(serve)} | kwargs))
    )
    assert len(runner.load_scopes(data, cache, allow_download=True)["BTCUSDT"]) == 3
    checksum = next(cache.rglob("*.CHECKSUM"))
    checksum.write_text("0" * 64 + "  " + checksum.name.removesuffix(".CHECKSUM"))
    with pytest.raises(ValueError, match="checksum"):
        runner.load_scopes(data, cache, allow_download=True)


@pytest.mark.parametrize("location", ["source", "cache", "prospective", "parent"])
def test_output_location_protections(tmp_path, location):
    cache = tmp_path / "cache"
    locations = {
        "source": ROOT / "HistoricalReplays/rejected",
        "cache": cache / "HistoricalReplays/rejected",
        "prospective": tmp_path / "ProspectiveStudies/HistoricalReplays/rejected",
        "parent": tmp_path / "WrongParent/rejected",
    }
    with pytest.raises(ValueError, match="separate|HistoricalReplays|protected"):
        runner.run_replay(ROOT, cache, locations[location])
    assert not locations[location].exists()


def test_registered_failure_retained_numbered_and_invalid_registry_refused(tmp_path, monkeypatch):
    parent = tmp_path / "HistoricalReplays"
    monkeypatch.setattr(
        runner, "load_scopes", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("restoration failed"))
    )
    for number in (1, 2):
        output = parent / f"round-{number}"
        with pytest.raises(RuntimeError, match="restoration failed"):
            runner.run_replay(ROOT, tmp_path / "cache", output)
        protocol = json.loads((output / "protocol.json").read_text())
        assert protocol["attempt_number"] == number
        assert protocol["prior_selection"]["trial_count"] == 24
        assert json.loads((output / "status.json").read_text())["status"] == "failed"
        assert not (output / "result.json").exists()
    records = [json.loads(line) for line in (parent / "campaigns.jsonl").read_text().splitlines()]
    assert records[1]["previous_hash"] == records[0]["hash"]
    with pytest.raises(FileExistsError):
        runner.run_replay(ROOT, tmp_path / "cache", output)
    with (parent / "campaigns.jsonl").open("a") as stream:
        stream.write("{}\n")
    with pytest.raises(ValueError, match="registry"):
        runner.run_replay(ROOT, tmp_path / "cache", parent / "round-3")
    assert not (parent / "round-3").exists()


def test_real_tiny_registered_run_repeatability_parallel_and_evidence(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    data, _ = fixture_discovery(cache)
    monkeypatch.setattr(runner, "load_pinned_discovery", lambda root: deepcopy(data))
    monkeypatch.setattr(runner.os, "cpu_count", lambda: 4)
    results = []
    for workers in (1, 2):
        output = tmp_path / "HistoricalReplays" / f"round-{workers}"
        result = runner.run_replay(ROOT, cache, output, workers=workers)
        assert json.loads((output / "status.json").read_text())["status"] == "succeeded"
        assert json.loads((output / "result.json").read_text()) == result
        assert result["assets"][0]["quality"]["missing_hours"] == 21
        assert result["assets"][0]["quality"]["internal_missing_hours"] == 1
        pids = []
        for asset in result["assets"]:
            pids.append(asset["worker_pid"])
            events = [
                json.loads(line)
                for line in (output / asset["candidate"]["symbol"] / "events.jsonl").read_text().splitlines()
            ]
            assert events[0]["type"] == "open"
            assert events[-1]["hash"] == asset["journal_head"]
            del asset["worker_pid"]
        if workers == 2:
            assert len(set(pids)) == 2 and os.getpid() not in pids
        results.append(result["assets"])
    assert results[0] == results[1]


@pytest.mark.parametrize("requested,cpus,expected", [(1, 1, 1), (2, 1, 1), (2, 3, 1), (2, 4, 2), (2, None, 1)])
def test_worker_cap_reserves_two_processors(requested, cpus, expected, monkeypatch):
    monkeypatch.setattr(runner.os, "cpu_count", lambda: cpus)
    assert runner.effective_workers(requested) == expected


@pytest.mark.parametrize("value", [0, 3, -1, True, 1.5])
def test_invalid_workers_fail_before_registration(tmp_path, value):
    with pytest.raises(ValueError, match="workers"):
        runner.run_replay(ROOT, tmp_path / "cache", tmp_path / "HistoricalReplays/round", workers=value)
    assert not (tmp_path / "HistoricalReplays").exists()


def test_interval_ending_periods_carry_losses_and_label_partial_years():
    curve = [
        {"at": "2025-01-01T00:00:00Z", "equity": "9900"},
        {"at": "2025-01-01T01:00:00Z", "equity": "9800"},
        {"at": "2025-02-01T00:00:00Z", "equity": "9700"},
    ]
    periods = period_changes(curve, "2024-12-31T23:00:00Z", "2025-02-01T00:00:00Z")
    assert [(p["period"], p["equity_change"]) for p in periods["years"]] == [("2024", "-100"), ("2025", "-200")]
    assert all(p["partial_calendar_period"] for p in periods["years"])
    assert periods["months"][1]["starting_equity"] == "9900"
    assert Decimal(periods["months"][1]["equity_change"]) == -200
    assert periods["months"][1]["partial_calendar_period"] is False
    assert periods["months"][1]["missing_hours"] == 742
    assert period_changes([], "2025-01-01T00:00:00Z", "2025-01-02T00:00:00Z")["years"][0]["return"] == "0"


def test_source_change_and_interrupt_keep_partial_events(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    data, _ = fixture_discovery(cache)
    monkeypatch.setattr(runner, "load_pinned_discovery", lambda root: data)
    original = runner.source_identity
    identities = iter([original(ROOT), "changed"])
    monkeypatch.setattr(runner, "source_identity", lambda root: next(identities))
    output = tmp_path / "HistoricalReplays/changed"
    with pytest.raises(RuntimeError, match="source"):
        runner.run_replay(ROOT, cache, output)
    assert (output / "BTCUSDT/events.jsonl").stat().st_size > 0
    assert json.loads((output / "status.json").read_text())["status"] == "failed"
    assert not (output / "result.json").exists()


def test_report_keeps_empty_trade_counts_and_execution_limits(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    data, _ = fixture_discovery(cache)
    monkeypatch.setattr(runner, "load_pinned_discovery", lambda root: data)
    result = runner.run_replay(ROOT, cache, tmp_path / "HistoricalReplays/report")
    report = render_report(result)
    for phrase in ("retrospective", "24", "partial", "doubled", "extra 34 bps", "observed", "0", "BTCUSDT", "ETHUSDT"):
        assert phrase in report
    assert "probability of profit" not in report.lower()


def test_callback_failure_aborts_attempt_without_reusing_replay(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    data, _ = fixture_discovery(cache)
    monkeypatch.setattr(runner, "load_pinned_discovery", lambda root: data)
    original_json = runner._json

    def failing_event(value):
        if value.get("type") == "close":
            raise OSError("event disk failure")
        return original_json(value)

    monkeypatch.setattr(runner, "_json", failing_event)
    output = tmp_path / "HistoricalReplays/callback"
    with pytest.raises(OSError, match="event disk failure"):
        runner.run_replay(ROOT, cache, output)
    events = (output / "BTCUSDT/events.jsonl").read_text().splitlines()
    assert len(events) == 1
    assert json.loads(events[0])["type"] == "open"
    assert not (output / "ETHUSDT").exists()
    assert json.loads((output / "status.json").read_text())["error_type"] == "OSError"


def test_interrupt_retains_registered_failure(tmp_path, monkeypatch):
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(runner, "load_scopes", interrupted)
    output = tmp_path / "HistoricalReplays/interrupted"
    with pytest.raises(KeyboardInterrupt):
        runner.run_replay(ROOT, tmp_path / "cache", output)
    assert json.loads((output / "status.json").read_text())["error_type"] == "KeyboardInterrupt"
    assert (output / "protocol.json").exists()


def test_archive_removed_after_preflight_cannot_trigger_network(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    data, _ = fixture_discovery(cache)
    original = runner.preflight_archives

    def remove_after_preflight(*args, **kwargs):
        original(*args, **kwargs)
        next(cache.rglob("*.zip")).unlink()

    monkeypatch.setattr(runner, "preflight_archives", remove_after_preflight)
    with pytest.raises(RuntimeError, match="network forbidden"):
        runner.load_scopes(data, cache)


def test_registration_precedes_outcome_reading_and_result_write_failure_is_failed(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    data, _ = fixture_discovery(cache)
    monkeypatch.setattr(runner, "load_pinned_discovery", lambda root: data)
    output = tmp_path / "HistoricalReplays/publication"
    original = runner.load_scopes

    def verify_registered(*args, **kwargs):
        assert (output / "protocol.json").exists()
        assert (output.parent / "campaigns.jsonl").read_text().strip()
        (output / "report.md").write_text("preexisting evidence")
        return original(*args, **kwargs)

    monkeypatch.setattr(runner, "load_scopes", verify_registered)
    with pytest.raises(FileExistsError):
        runner.run_replay(ROOT, cache, output)
    assert (output / "report.md").read_text() == "preexisting evidence"
    assert json.loads((output / "status.json").read_text())["status"] == "failed"


def test_missing_completed_protocol_refuses_registry_reuse(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    data, _ = fixture_discovery(cache)
    monkeypatch.setattr(runner, "load_pinned_discovery", lambda root: data)
    parent = tmp_path / "HistoricalReplays"
    runner.run_replay(ROOT, cache, parent / "first")
    (parent / "first/protocol.json").unlink()
    with pytest.raises(ValueError, match="registry"):
        runner.run_replay(ROOT, cache, parent / "second")
    assert not (parent / "second").exists()


def test_parallel_worker_failure_retains_other_partial_evidence(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    data, _ = fixture_discovery(cache)
    monkeypatch.setattr(runner, "load_pinned_discovery", lambda root: data)
    monkeypatch.setattr(runner.os, "cpu_count", lambda: 4)
    original = runner.load_scopes

    def invalid_scopes(*args, **kwargs):
        scopes = original(*args, **kwargs)
        scopes["BTCUSDT"].loc[1, "high"] = 1
        return scopes

    monkeypatch.setattr(runner, "load_scopes", invalid_scopes)
    output = tmp_path / "HistoricalReplays/worker-failure"
    with pytest.raises((ValueError, RuntimeError), match="OHLC"):
        runner.run_replay(ROOT, cache, output, workers=2)
    assert (output / "BTCUSDT/events.jsonl").stat().st_size > 0
    assert json.loads((output / "status.json").read_text())["status"] == "failed"
    assert not (output / "result.json").exists()


def test_retained_discovery_preserves_original_pinned_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "load_scopes", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline")))
    output = tmp_path / "HistoricalReplays/discovery"
    with pytest.raises(RuntimeError):
        runner.run_replay(ROOT, tmp_path / "cache", output)
    assert hashlib.sha256((output / "discovery.json").read_bytes()).hexdigest() == runner.DISCOVERY_SHA256


def test_period_without_closes_carries_equity_and_zero_start_has_no_return():
    curve = [{"at": "2025-02-01T00:00:00Z", "equity": "0"}, {"at": "2025-04-01T00:00:00Z", "equity": "10"}]
    months = period_changes(curve, "2025-01-01T00:00:00Z", "2025-04-01T00:00:00Z")["months"]
    assert [p["period"] for p in months] == ["2025-01", "2025-02", "2025-03"]
    assert months[1]["starting_equity"] == months[1]["ending_equity"] == "0"
    assert months[1]["missing_hours"] == 672
    assert months[2]["return"] is None


def test_shared_registry_symlink_refused_without_writing_target(tmp_path):
    parent = tmp_path / "HistoricalReplays"
    parent.mkdir()
    unrelated = tmp_path / "unrelated"
    unrelated.write_text("")
    (parent / "campaigns.jsonl").symlink_to(unrelated)
    with pytest.raises(ValueError, match="registry"):
        runner.run_replay(ROOT, tmp_path / "cache", parent / "round")
    assert unrelated.read_text() == ""


@pytest.mark.parametrize("field", ["selected_rows", "invalid_boundary_rows"])
def test_loader_requires_exact_retained_parser_counts(tmp_path, field):
    data, _ = fixture_discovery(tmp_path / "cache")
    data["archive_manifests"][0][field] += 1
    data["archive_manifest_hash"] = canonical_hash(data["archive_manifests"])
    with pytest.raises(ValueError, match="loaded archive manifest"):
        runner.load_scopes(data, tmp_path / "cache")


def test_identical_archive_duplicates_are_rejected_even_when_parser_deduplicates(tmp_path):
    cache = tmp_path / "cache"
    data, _ = fixture_discovery(cache)
    item = data["archive_manifests"][0]
    path = next(cache.rglob(item["name"]))
    with zipfile.ZipFile(io.BytesIO(path.read_bytes())) as archive:
        name = archive.namelist()[0]
        raw = archive.read(name)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, raw + b"\n" + raw.splitlines()[0])
    payload = stream.getvalue()
    item.update(sha256=hashlib.sha256(payload).hexdigest(), compressed_bytes=len(payload), selected_rows=4)
    path.write_bytes(payload)
    path.with_name(path.name + ".CHECKSUM").write_text(f"{item['sha256']}  {item['name']}\n")
    data["archive_manifest_hash"] = canonical_hash(data["archive_manifests"])
    with pytest.raises(ValueError, match="duplicate"):
        runner.load_scopes(data, cache)


def test_cli_offline_failure_is_retained_and_returns_nonzero(tmp_path, capsys):
    output = tmp_path / "HistoricalReplays/cli"
    code = runner.main(["--root", str(ROOT), "--cache-dir", str(tmp_path / "cache"), "--output-dir", str(output)])
    assert code == 2
    assert "offline" in capsys.readouterr().err
    assert json.loads((output / "status.json").read_text())["status"] == "failed"


def test_successful_cli_emits_machine_readable_result_location(tmp_path, capsys, monkeypatch):
    cache = tmp_path / "cache"
    data, _ = fixture_discovery(cache)
    monkeypatch.setattr(runner, "load_pinned_discovery", lambda root: data)
    output = tmp_path / "HistoricalReplays/cli"
    assert runner.main(["--root", str(ROOT), "--cache-dir", str(cache), "--output-dir", str(output)]) == 0
    assert json.loads(capsys.readouterr().out) == dict(status="succeeded", output_dir=str(output))
