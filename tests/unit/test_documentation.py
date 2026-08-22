from __future__ import annotations

from pathlib import Path


def test_readme_is_recruiter_ready_and_documents_definition_of_done_commands():
    root = Path(__file__).resolve().parents[2]
    text = (root / "README.md").read_text(encoding="utf-8")

    assert text.startswith("# Alternative-Data Earnings Nowcaster")
    for command in ("make demo", "make test", "make dashboard"):
        assert command in text
    for section in ("Investment thesis", "Architecture", "Data sources", "Measured demo results", "macOS setup"):
        assert section in text
    assert "not investment advice" in text.lower()
    assert "not wall street consensus" in text.lower()
    assert "not affiliated with or endorsed by goldman sachs" in text.lower()


def test_documentation_links_and_dashboard_images_exist():
    root = Path(__file__).resolve().parents[2]
    for path in (
        "docs/architecture.md",
        "docs/methodology.md",
        "docs/data_dictionary.md",
        "docs/interview_guide.md",
        "scripts/capture_dashboard.py",
    ):
        assert (root / path).is_file(), path
    expected_images = {
        "overview.png",
        "company_research.png",
        "forecast_monitor.png",
        "model_performance.png",
        "event_study.png",
        "data_quality.png",
    }
    assert expected_images <= {path.name for path in (root / "docs" / "images").glob("*.png")}
