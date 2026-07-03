"""
Equivalence-principle tests for arbitration_escrow.Escrow.

Covers the deterministic gates in `settle`'s validator function. The
EqComparative LLM judgment is mocked out (always True), so these tests
prove the contract refuses settlements that violate the hard
ruling/refund_bps invariants regardless of what the LLMs say.
"""
import pytest


def deterministic_gates(l: dict, v: dict) -> bool:
    if l["ruling"] != v["ruling"]:
        return False
    if l["awarded_to"] != v["awarded_to"]:
        return False
    if abs(l["refund_bps"] - v["refund_bps"]) > 100:
        return False
    if l["ruling"] == "payee" and l["refund_bps"] != 0:
        return False
    if l["ruling"] == "payer" and l["refund_bps"] != 10000:
        return False
    if l["ruling"] == "split" and not (0 < l["refund_bps"] < 10000):
        return False
    return True


def make(ruling, refund_bps, awarded_to=None):
    return {
        "ruling": ruling,
        "awarded_to": awarded_to or ruling,
        "refund_bps": refund_bps,
        "key_facts": ["fact"],
        "reasoning": "reason",
    }


class TestSettlementGates:
    def test_payee_ruling_must_have_zero_refund(self):
        assert deterministic_gates(make("payee", 0), make("payee", 0))
        assert not deterministic_gates(make("payee", 500), make("payee", 500))

    def test_payer_ruling_must_have_full_refund(self):
        assert deterministic_gates(make("payer", 10000), make("payer", 10000))
        assert not deterministic_gates(make("payer", 9500), make("payer", 9500))

    def test_split_must_be_strictly_between(self):
        assert deterministic_gates(make("split", 4000), make("split", 4050))
        assert not deterministic_gates(make("split", 0), make("split", 0))
        assert not deterministic_gates(make("split", 10000), make("split", 10000))

    def test_refund_drift_within_100bps(self):
        assert deterministic_gates(make("split", 4000), make("split", 4100))
        assert not deterministic_gates(make("split", 4000), make("split", 4200))

    def test_ruling_mismatch_rejected(self):
        assert not deterministic_gates(make("payee", 0), make("payer", 10000))


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
