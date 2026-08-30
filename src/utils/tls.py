"""Verified client TLS for packaged runtimes without a system certificate store."""

from __future__ import annotations

import ssl

import certifi


def verified_client_context() -> ssl.SSLContext:
    """Use the bundled public CA roots; retain certificate and hostname checks."""
    return ssl.create_default_context(cafile=certifi.where())
