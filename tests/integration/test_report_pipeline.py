from __future__ import annotations

from src.reporting.case_study import select_case_study
from src.reporting.research_report import REQUIRED_REPORT_SECTIONS, generate_research_report
from src.reporting.summary import research_statistics


def test_demo_outputs_generate_evidence_backed_report_and_case(tmp_path, demo_database):
    _, database = demo_database
    statistics = research_statistics(database)

    report = generate_research_report(database, tmp_path / "latest_research_report.md").read_text()
    case = select_case_study(database)

    assert all(section in report for section in REQUIRED_REPORT_SECTIONS)
    assert f"{statistics['companies']} companies" in report
    assert f"{statistics['company_quarters']} company-quarters" in report
    assert case is not None
    assert case.company_id in report
    assert "not investment advice" in report.lower()
