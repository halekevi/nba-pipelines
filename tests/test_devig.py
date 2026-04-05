from proporacle.pricing.devig import devig_two_way


def test_proportional_sums_one():
    a, b = devig_two_way(-110, -110, method="proportional")
    assert abs(a + b - 1.0) < 1e-6
