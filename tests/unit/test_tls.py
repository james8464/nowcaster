from __future__ import annotations

import asyncio
import ssl

import certifi
import pytest

from src.live_monitor import providers
from src.trading import stream as broker_stream


def _assert_verified(context):
    assert isinstance(context, ssl.SSLContext)
    assert context.check_hostname
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2
    assert context.get_ca_certs()


def test_stream_tls_uses_available_ca_bundle_with_verification_enabled(monkeypatch, tmp_path):
    from src.utils.tls import verified_client_context

    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "missing-system-certificates.pem"))
    _assert_verified(verified_client_context())


def test_stream_tls_never_falls_back_to_unverified_when_bundle_is_missing(monkeypatch, tmp_path):
    from src.utils.tls import verified_client_context

    monkeypatch.setattr(certifi, "where", lambda: str(tmp_path / "missing-bundle.pem"))
    with pytest.raises(OSError):
        verified_client_context()


@pytest.mark.parametrize("provider", ["binance", "alpaca"])
def test_market_stream_connectors_receive_the_verified_certificate_context(monkeypatch, provider):
    captured = {}
    message = '{"result":null,"id":1}' if provider == "binance" else '[{"T":"success","msg":"connected"}]'

    class Socket:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def send(self, _payload):
            pass

        async def recv(self):
            return message

        def __aiter__(self):
            return self

        async def __anext__(self):
            return message

    def connector(_url, **options):
        captured.update(options)
        return Socket()

    monkeypatch.setattr(providers, "connect", connector)
    adapter = (
        providers.BinanceSpotAdapter()
        if provider == "binance"
        else providers.AlpacaMarketDataAdapter("iex", "test-key", "test-secret")
    )

    async def observe():
        events = adapter.stream("wss://example.test/stream", ("BTCUSDT",))
        try:
            await anext(events)
        finally:
            await events.aclose()

    asyncio.run(observe())
    _assert_verified(captured.get("ssl"))


def test_paper_broker_stream_connector_receives_the_verified_certificate_context(monkeypatch):
    captured = {}

    async def connector(_url, **options):
        captured.update(options)
        return object()

    monkeypatch.setattr("websockets.asyncio.client.connect", connector)
    asyncio.run(broker_stream._default_connector("wss://example.test/stream"))
    _assert_verified(captured.get("ssl"))
