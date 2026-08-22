from __future__ import annotations

from src.reporting.case_study import select_case_study
from src.reporting.recruiter import generate_resume_bullets, recruiter_statistics
from src.reporting.research_report import REQUIRED_REPORT_SECTIONS, generate_research_report


def test_demo_outputs_generate_evidence_backed_report_case_and_resume_bullets(tmp_path, demo_database):
    _, database = demo_database
    statistics = recruiter_statistics(database)

    report = generate_research_report(database, tmp_path / "latest_research_report.md").read_text()
    bullets = generate_resume_bullets(database, tmp_path / "resume_bullets.md").read_text()
    case = select_case_study(database)

    assert all(section in report for section in REQUIRED_REPORT_SECTIONS)
    assert f"{statistics['companies']} companies" in report
    assert f"{statistics['company_quarters']} company-quarters" in report
    assert case is not None
    assert case.company_id in report
    assert f"{statistics['historical_forecasts']:,}" in bullets
    assert "X%" not in bullets
    assert "not investment advice" in report.lower()
