from src.strategy import moving_average, sma_crossover_signal


def test_moving_average_basic():
    assert moving_average([1, 2, 3, 4], 2) == 3.5
    assert moving_average([1, 2], 5) is None


def test_hold_when_not_enough_data():
    assert sma_crossover_signal([1, 2, 3], fast=2, slow=3) == "hold"


def test_bullish_crossover():
    # descente régulière puis rebond -> la MM courte repasse au-dessus sur la dernière barre
    closes = [20, 19, 18, 17, 16, 15, 14, 13, 12, 11, 10, 12, 14]
    assert sma_crossover_signal(closes, fast=3, slow=5) == "buy"


def test_bearish_crossover():
    closes = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 18, 16]
    assert sma_crossover_signal(closes, fast=3, slow=5) == "sell"


def test_hold_when_no_cross():
    closes = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]
    assert sma_crossover_signal(closes, fast=3, slow=5) == "hold"
