from scripts.generate_sbom import build_sbom


def test_sbom_is_deterministic_sorted_and_contains_no_environment_values(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("APCA_API_SECRET_KEY", "must-not-appear")
    first = build_sbom(tmp_path)
    second = build_sbom(tmp_path)
    assert first == second
    components = first["components"]
    assert components == sorted(components, key=lambda item: (item["name"].lower(), item["version"]))
    assert "must-not-appear" not in str(first)
