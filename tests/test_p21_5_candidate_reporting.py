from scripts.test_windows_candidate_lifecycle import is_known_unsigned_p212_startup_block


def test_only_known_unsigned_p212_cp1252_startup_is_reported_blocked():
    evidence = (
        "UnicodeEncodeError: 'charmap' codec can't encode characters "
        "(cp1252) in brain/server_launch.py"
    )
    assert is_known_unsigned_p212_startup_block('NotSigned', evidence)
    assert not is_known_unsigned_p212_startup_block('SignedPresent', evidence)
    assert not is_known_unsigned_p212_startup_block('NotSigned', 'Brain startup timed out')
