import json

from tests.background_acceptance_processes import read_evidence, scoped_processes


def test_only_installed_app_and_helpers_are_observed():
    installed = (
        "701 Wed Sep 30 13:00:00 2026 /Applications/Nowcaster.app/Contents/Helpers/"
        "nowcaster-engine strategy background-research"
    )
    unrelated = "702 Wed Sep 30 13:00:00 2026 /Applications/Other.app/Contents/MacOS/Other private-argument"
    assert scoped_processes(installed + "\n" + unrelated) == [installed]


def test_preserves_exact_pid_birth_and_arguments():
    rows = [
        "   71 1 Wed Sep 30 13:00:00 2026 /Applications/Nowcaster.app/Contents/MacOS/Nowcaster",
        "72 71 Wed Sep 30 13:00:01 2026 /Applications/Nowcaster.app/Contents/Helpers/"
        "nowcaster-engine strategy background-research --registry-directory /Users/test/With Spaces",
    ]
    assert scoped_processes("\n".join(rows)) == rows


def test_evidence_is_read_only_and_reports_absence_without_fabricating_health(tmp_path):
    source, registry = tmp_path / "source", tmp_path / "registry"
    source.mkdir()
    controls = registry / "controls"
    controls.mkdir(parents=True)
    original = json.dumps({"run_id": "test", "state": "paused", "nonce": "private-test-marker"})
    control = controls / "test.control.json"
    control.write_text(original)
    evidence = read_evidence(source, registry)
    assert evidence["collector"] == {"observation_error": "FileNotFoundError"}
    assert evidence["controls"] == [{"run_id": "test", "state": "paused", "updated_at": None}]
    assert control.read_text() == original
