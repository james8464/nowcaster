"""Release-only verification of the standalone, source-independent collector."""

import hashlib
import json
import os
import ssl
import subprocess
import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from src.research.round_two_contracts import ResearchRoundProtocol
from src.research.round_two_registry import register_round
from src.research.trend_advisor import POLICY
from src.strategies.types import canonical_hash


@pytest.fixture
def market_proxy(tmp_path):
    """Substitute only the external HTTPS service, leaving the frozen helper real."""
    certificate, key = tmp_path / "certificate.pem", tmp_path / "key.pem"
    subprocess.run(
        [
            "/usr/bin/openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=data-api.binance.vision",
            "-addext",
            "subjectAltName=DNS:data-api.binance.vision",
            "-keyout",
            str(key),
            "-out",
            str(certificate),
        ],
        check=True,
        capture_output=True,
        timeout=10,
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate, key)

    class MarketData(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            url = urlsplit(self.path)
            query = parse_qs(url.query)
            now_ms = int(datetime.now(UTC).timestamp() * 1000)
            opened = now_ms // 60000 * 60000 - 60000
            if url.path == "/api/v3/time":
                payload = {"serverTime": now_ms}
            elif url.path == "/api/v3/klines":
                payload = [
                    [opened, "100", "102", "99", "101", "1000", opened + 59999, "101000", 10, "500", "50500", "0"]
                ]
            elif url.path == "/api/v3/ticker/bookTicker":
                payload = {"symbol": query["symbol"][0], "bidPrice": "100.99", "askPrice": "101.01"}
            else:
                self.send_error(404)
                return
            data = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    class Proxy(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_CONNECT(self):
            if self.path != "data-api.binance.vision:443":
                self.send_error(403)
                return
            self.send_response(200)
            self.end_headers()
            with context.wrap_socket(self.connection, server_side=True) as connection:
                MarketData(connection, self.client_address, self.server)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {"https_proxy": f"http://127.0.0.1:{server.server_port}", "SSL_CERT_FILE": str(certificate)}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.skipif(not os.environ.get("NOWCASTER_PAPER_HELPER"), reason="requires a built release helper")
def test_packaged_helper_reads_registered_state_without_python_or_checkout(tmp_path):
    helper = Path(os.environ["NOWCASTER_PAPER_HELPER"]).resolve()
    protocol = ResearchRoundProtocol.default(round_id="bundle-verification", starts_at=datetime.now(UTC))
    directory = tmp_path / "evidence"
    register_round(protocol, directory)
    retained = (directory / "protocol.json").read_bytes()
    started = time.monotonic()
    result = subprocess.run(
        [str(helper), "status", "--directory", str(directory)],
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "TMPDIR": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert time.monotonic() - started < 15, "helper must respond within the native request deadline"
    state = json.loads(result.stdout)
    assert state["kind"] == "stopped"
    assert state["protocol_hash"] == protocol.identity_hash
    assert state["suggestion"] is None
    assert (directory / "protocol.json").read_bytes() == retained
    assert sorted(path.name for path in directory.iterdir()) == ["protocol.json"]

    # The causal evaluator binds exact source bytes and its static registry.
    # These are runtime inputs, not a dependency on the original checkout.
    resources = helper.parents[1] / "Resources"
    source = Path(__file__).resolve().parents[2]
    required = [Path("config/strategies.yaml"), Path("src/research/round_two_walkforward.py")]
    required.extend(path.relative_to(source) for path in (source / "src/strategies").rglob("*.py"))
    for relative in required:
        assert (resources / relative).read_bytes() == (source / relative).read_bytes()


@pytest.mark.skipif(not os.environ.get("NOWCASTER_PAPER_HELPER"), reason="requires a built release helper")
def test_packaged_helper_evaluates_live_bar_with_retained_protocol(tmp_path, market_proxy):
    """Catch missing source resources on the actual signed helper evaluation path.

    A finalized one-minute bar is eligible only for its first 15 seconds, so
    allow one full real-clock boundary through the local market-data proxy.
    Failure to collect/evaluate is a release failure, never a skipped success.
    """
    helper = Path(os.environ["NOWCASTER_PAPER_HELPER"]).resolve()
    protocol = ResearchRoundProtocol.default(round_id="bundle-evaluation", starts_at=datetime.now(UTC))
    directory = tmp_path / "evidence"
    register_round(protocol, directory)
    retained = (directory / "protocol.json").read_bytes()
    summary = directory / "research-round-2-summary.json"
    deadline = time.monotonic() + 80
    while True:
        result = subprocess.run(
            [str(helper), "run-once", "--directory", str(directory)],
            cwd=tmp_path,
            env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "TMPDIR": str(tmp_path), **market_proxy},
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        state = json.loads(result.stdout)
        assert "retained_evidence_unavailable" not in state["reasons"], state
        if summary.exists() or time.monotonic() >= deadline:
            break
        time.sleep(2)
    assert summary.exists(), f"Packaged helper did not evaluate a finalized bar: {state}"
    report = json.loads(summary.read_text())
    assert report["protocolHash"] == protocol.identity_hash
    assert report["trendAdvisor"]
    source = Path(__file__).resolve().parents[2]
    relative = Path("src/research/trend_advisor.py")
    source_bytes = (source / relative).read_bytes()
    assert (helper.parents[1] / "Resources" / relative).read_bytes() == source_bytes
    policy_hash = canonical_hash({"policy": POLICY, "implementation": hashlib.sha256(source_bytes).hexdigest()})
    assert all(item["policyHash"] == policy_hash for item in report["trendAdvisor"])
    assert (directory / "protocol.json").read_bytes() == retained
