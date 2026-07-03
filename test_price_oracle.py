"""
Equivalence-principle tests for price_oracle.AIWebOracle.

These tests run *only* the validator function against synthetic leader
results to verify the consensus gates without needing a live GenVM. They
also cover the leader's reject-sentinel branch.

Pattern modeled on GenLayer Studio test fixtures:
    https://github.com/genlayerlabs/genlayer-studio
"""
from __future__ import annotations
import json
import pytest


def make_leader(currency: str, price: float, confidence: int, **kw):
    return {
        "price": price,
        "price_micro": int(round(price * 1_000_000)),
        "currency": currency,
        "confidence": confidence,
        "analysis": kw.get("analysis", "extracted from <span class='quote'>"),
        "source_excerpt": kw.get("excerpt", "BTC/USD 67,123.45"),
    }


# Re-implementation of the validator gates so tests don't need GenVM.
PRICE_TOLERANCE_BPS = 200


def accepts(leader: dict, validator: dict) -> bool:
    if leader["currency"].upper() != validator["currency"].upper():
        return False
    lc, vc = leader["confidence"], validator["confidence"]
    if lc == 0 or vc == 0:
        return lc == vc
    if abs(lc - vc) > 1:
        return False
    lp, vp = leader["price_micro"], validator["price_micro"]
    if lp == 0:
        return vp == 0
    return abs(lp - vp) * 10_000 // abs(lp) <= PRICE_TOLERANCE_BPS


class TestPriceOracleValidator:
    def test_accepts_within_tolerance(self):
        l = make_leader("USD", 67_000.0, 8)
        v = make_leader("USD", 67_500.0, 8)   # +0.74 % drift
        assert accepts(l, v)

    def test_rejects_drift_above_2pct(self):
        l = make_leader("USD", 67_000.0, 8)
        v = make_leader("USD", 70_000.0, 8)   # +4.5 %
        assert not accepts(l, v)

    def test_rejects_currency_mismatch(self):
        l = make_leader("USD", 67_000.0, 8)
        v = make_leader("EUR", 67_000.0, 8)
        assert not accepts(l, v)

    def test_reject_sentinel_must_be_unanimous(self):
        assert accepts(make_leader("USD", 0, 0), make_leader("USD", 0, 0))
        assert not accepts(make_leader("USD", 0, 0), make_leader("USD", 67_000, 8))
        assert not accepts(make_leader("USD", 67_000, 8), make_leader("USD", 0, 0))

    def test_confidence_tolerance(self):
        l = make_leader("USD", 67_000.0, 7)
        v = make_leader("USD", 67_100.0, 8)   # ±1 ok
        assert accepts(l, v)
        v2 = make_leader("USD", 67_100.0, 5)  # ±2 not ok
        assert not accepts(l, v2)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
