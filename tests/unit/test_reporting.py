from __future__ import annotations

from src.database.engine import Database
from src.reporting.recruiter import generate_resume_bullets, recruiter_statistics
from src.reporting.research_report import REQUIRED_REPORT_SECTIONS, generate_research_report


def test_empty_database_report_has_all_sections_and_no_fabricated_claims(tmp_path):
    database = Database.from_url(f"duckdb:///{tmp_path / 'empty.duckdb'}")
    database.initialize()

    report = generate_research_report(database, tmp_path / "report.md").read_text()
    bullets = generate_resume_bullets(database, tmp_path / "bullets.md").read_text()

    assert all(section in report for section in REQUIRED_REPORT_SECTIONS)
    assert "insufficient evidence" in report.lower()
    assert "not generated" in bullets.lower()
    assert "X%" not in bullets


def test_recruiter_statistics_are_zeros_not_placeholders_for_empty_database(tmp_path):
    database = Database.from_url(f"duckdb:///{tmp_path / 'stats.duckdb'}")
    database.initialize()

    statistics = recruiter_statistics(database)

    assert statistics["companies"] == 0
    assert statistics["forecast_mae_improvement"] is None
    assert statistics["event_spread"] is None
