from __future__ import annotations

from src.utils.provenance import canonical_hash, git_commit


def test_canonical_hash_is_order_independent():
    assert canonical_hash({"b": 2, "a": 1}) == canonical_hash({"a": 1, "b": 2})


def test_git_commit_returns_current_revision():
    value = git_commit()
    assert len(value) == 40
    assert set(value) <= set("0123456789abcdef")
