# calculator.py - synthetic arithmetic helper
# BUG: divide() should perform integer floor division for the allocation
# use-case described in README, but currently uses float division.

def add(a, b):
    return a + b


def subtract(a, b):
    return a - b


def multiply(a, b):
    return a * b


def divide(a, b):
    if b == 0:
        raise ValueError("division by zero")
    return a / b
