"""Pure budget DTO and input boundary cases; no database or provider calls."""
from uuid import uuid4

import pytest

from mirofish_execution.budget import InvalidBudget, _fingerprint, _money, _uuid


@pytest.mark.parametrize("value", [None, True, False, 0, -1, 1.0, "1", 2**63])
def test_ceiling_rejects_implicit_or_non_integer_money(value):
    with pytest.raises(InvalidBudget):
        _money(value)


def test_ceiling_accepts_integer_range():
    assert _money(1) == 1
    assert _money(2**63 - 1) == 2**63 - 1


@pytest.mark.parametrize("value", [None, True, "A" * 64, "0" * 63, "g" * 64])
def test_fingerprint_is_exact_lowercase_sha256(value):
    with pytest.raises(InvalidBudget):
        _fingerprint(value)


def test_canonical_uuid_input():
    identifier = uuid4()
    assert _uuid(identifier) == identifier
    with pytest.raises(InvalidBudget):
        _uuid("not-a-uuid")
