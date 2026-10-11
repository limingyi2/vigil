# test_calculator.py
from calculator import add, subtract, multiply, divide


def test_add():
    assert add(2, 3) == 5


def test_subtract():
    assert subtract(10, 4) == 6


def test_multiply():
    assert multiply(3, 4) == 12


def test_divide_integer():
    # divide() must return integer floor division for batch allocation.
    assert divide(10, 3) == 3
    assert divide(9, 3) == 3
    assert divide(7, 2) == 3
