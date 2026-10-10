from buggy_module import add_tax

def test_add_tax():
    assert round(add_tax(100, 0.1), 2) == 110
