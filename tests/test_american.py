from proporacle.pricing.american import profit_multiple, to_decimal_odds, to_implied_prob


def test_implied_even():
    assert abs(to_implied_prob(-110) - (110 / 210)) < 1e-9


def test_profit_multiple_favorite():
    assert abs(profit_multiple(-200) - 0.5) < 1e-9


def test_decimal():
    assert abs(to_decimal_odds(100) - 2.0) < 1e-9
