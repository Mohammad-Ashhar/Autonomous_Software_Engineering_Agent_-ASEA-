from logic import is_even, is_positive


def test_even_number():
    assert is_even(4) is True


def test_odd_number():
    assert is_even(5) is False


def test_zero_is_even():
    assert is_even(0) is True


def test_positive_number():
    assert is_positive(5) is True


def test_negative_number():
    assert is_positive(-5) is False


def test_zero_is_not_positive():
    assert is_positive(0) is False
