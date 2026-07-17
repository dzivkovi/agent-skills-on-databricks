"""Creds-free unit tests for the volume janitor's age predicate (scripts/prune_outputs.py).

The live deletion is exercised by hand against the workspace; what is worth pinning here is the
one decision that can lose data if it is wrong: which files count as "old". The safety rule is
that an undateable file is never deleted.
"""
import prune_outputs

DAY = prune_outputs.DAY_MS
NOW = 1_700_000_000_000  # a fixed "now" in ms, so these tests never depend on the clock


def test_older_than_is_strict_about_the_window():
    assert prune_outputs.older_than(NOW - 8 * DAY, NOW, 7) is True
    assert prune_outputs.older_than(NOW - 6 * DAY, NOW, 7) is False
    # exactly N days is NOT older than N days (boundary is exclusive).
    assert prune_outputs.older_than(NOW - 7 * DAY, NOW, 7) is False


def test_unknown_mtime_is_never_old():
    # A file whose modification time we cannot read must survive - the janitor only ever removes
    # what it can prove is stale.
    assert prune_outputs.older_than(None, NOW, 7) is False
    assert prune_outputs.older_than(None, NOW, 0) is False
