from proporacle.monitoring.metrics import brier_score, ece_bins, log_loss


def test_brier():
    assert abs(brier_score([1, 0], [0.7, 0.4]) - ((0.3) ** 2 + (0.4) ** 2) / 2) < 1e-9


def test_log_loss_finite():
    v = log_loss([1, 0], [0.7, 0.3])
    assert v == v and v < 10


def test_ece_non_negative():
    assert ece_bins([1, 0, 1, 0], [0.9, 0.1, 0.8, 0.2], n_bins=5) >= 0.0
