from proporacle.pricing.ev import ev_per_unit


def test_ev_even_money_fair_coin():
    # +100: b=1, p=0.5 -> EV = 0
    assert abs(ev_per_unit(0.5, 100)) < 1e-9
