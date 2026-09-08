from src.research.prospective_statistics import bootstrap_screen


def test_bootstrap_rejects_insufficient_days_and_tail_resolution():
    assert bootstrap_screen([0.01] * 6, study_number=1, candidate_count=1)["lower_bound"] is None
    assert bootstrap_screen([0.01] * 90, study_number=10, candidate_count=2)["lower_bound"] is None


def test_bootstrap_deterministic_and_keeps_zero_and_negative_days():
    positive = bootstrap_screen([0.01] * 90, study_number=1, candidate_count=2)
    assert abs(positive["lower_bound"] - 0.01) < 1e-12
    assert positive == bootstrap_screen([0.01] * 90, study_number=1, candidate_count=2)
    mixed = bootstrap_screen([0.0] * 83 + [-0.02] * 7, study_number=1, candidate_count=2)
    assert mixed["lower_bound"] < 0
    assert mixed["observations"] == 90
