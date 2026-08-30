from __future__ import annotations

import shutil

import pytest

from src.utils import provenance
from src.utils.provenance import canonical_hash, git_commit


def test_canonical_hash_is_order_independent():
    assert canonical_hash({"b": 2, "a": 1}) == canonical_hash({"a": 1, "b": 2})


def test_git_commit_returns_current_revision():
    value = git_commit()
    assert len(value) == 40
    assert set(value) <= set("0123456789abcdef")


def test_research_source_identity_tracks_content_not_checkout_or_receipts(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    (first / "src").mkdir(parents=True)
    (first / "config").mkdir()
    (first / "src/strategy.py").write_text("threshold = 1\n")
    (first / "config/policy.yaml").write_text("cost: 1\n")
    (first / "pyproject.toml").write_text("[project]\nname = 'example'\n")
    original = provenance.research_source_hash(first)
    shutil.copytree(first, second)
    (second / ".git").mkdir()
    (second / ".git/HEAD").write_text("a different commit\n")
    (second / "README.md").write_text("Updated documentation\n")
    (second / "src/__pycache__").mkdir()
    (second / "src/__pycache__/strategy.pyc").write_bytes(b"generated cache")

    assert provenance.research_source_hash(second) == original
    (second / "src/strategy.py").write_text("threshold = 2\n")
    assert provenance.research_source_hash(second) != original
    (second / "src/strategy.py").write_text("threshold = 1\n")
    (second / "config/policy.yaml").write_text("cost: 2\n")
    assert provenance.research_source_hash(second) != original


def test_research_source_identity_rejects_unavailable_source(tmp_path):
    with pytest.raises(ValueError, match="source"):
        provenance.research_source_hash(tmp_path)
