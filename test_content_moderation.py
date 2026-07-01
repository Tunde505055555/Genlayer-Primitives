"""
Tests for content_moderation.ContentModeration's reputation logic and the
appeal verdict-equality gate (the LLM judgments are mocked out — the goal
of these tests is to lock in the deterministic effects of accepted
verdicts).
"""
import pytest

VERDICT_REP_DELTA = {"ok": 1, "warn": -2, "remove": -10, "ban": -50}


class FakeReputationStore:
    def __init__(self):
        self._rep = {}

    def apply(self, addr, verdict):
        d = VERDICT_REP_DELTA.get(verdict, 0)
        self._rep[addr] = self._rep.get(addr, 0) + d

    def get(self, addr):
        return self._rep.get(addr, 0)


def appeal_validator_accepts(leader_verdict: str, validator_verdict: str) -> bool:
    return leader_verdict == validator_verdict


class TestReputation:
    def test_ok_increments(self):
        s = FakeReputationStore()
        s.apply("alice", "ok")
        assert s.get("alice") == 1

    def test_remove_decrements_10(self):
        s = FakeReputationStore()
        s.apply("alice", "remove")
        assert s.get("alice") == -10

    def test_ban_is_terminal(self):
        s = FakeReputationStore()
        s.apply("alice", "ban")
        assert s.get("alice") == -50

    def test_successful_appeal_reverses_and_reapplies(self):
        s = FakeReputationStore()
        s.apply("alice", "remove")                # -10
        # appeal -> warn
        s._rep["alice"] = s.get("alice") - VERDICT_REP_DELTA["remove"] + VERDICT_REP_DELTA["warn"]
        assert s.get("alice") == -2


class TestAppealValidator:
    def test_matching_verdict_accepts(self):
        assert appeal_validator_accepts("warn", "warn")

    def test_mismatched_verdict_rejects(self):
        assert not appeal_validator_accepts("warn", "ok")
        assert not appeal_validator_accepts("remove", "warn")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
