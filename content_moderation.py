# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
"""
Content Moderation with Reputation
==================================

A subjective moderation queue. Anyone can submit a post (or a URL pointing
to one) along with the community's written guidelines. The contract reaches
consensus on a structured verdict and updates a per-author reputation score.

Consensus pattern
-----------------
Pattern: Source-Grounded Non-Comparative Validation.

Moderation is the canonical case where two reasonable validators will write
different rationales but should agree on the verdict. Generating *two*
independent verdicts and trying to align them is fragile — one validator
inevitably disagrees on edge cases.

Instead, the validator does **not** produce its own verdict. It uses the
`EqNonComparativeValidator` template to judge whether the *leader's* verdict
is defensible given the same source content and the same written criteria.

Why this is useful
------------------
- Drop-in moderation for any UGC surface that already publishes a code of
  conduct.
- The reputation score is a side-effect of accepted verdicts, so it inherits
  consensus for free.
- Disputes are first-class: an author can call `appeal()` which re-runs the
  same block with a richer prompt and (if accepted) reverses the action.
"""

from genlayer import *
import json
import genlayer.gl._internal.gl_call as gl_call
from genlayer.gl.nondet import _decode_nondet


VERDICT_OK = "ok"
VERDICT_WARN = "warn"
VERDICT_REMOVE = "remove"
VERDICT_BAN = "ban"

VERDICT_REP_DELTA = {
    VERDICT_OK: 1,
    VERDICT_WARN: -2,
    VERDICT_REMOVE: -10,
    VERDICT_BAN: -50,
}


class Post:
    id: str
    author: Address
    content_url: str
    content_snapshot: str
    verdict: str
    severity: u8
    violated_rules: str  # comma-separated rule ids
    rationale: str
    finalized: bool
    appealed: bool


class ContentModeration(gl.Contract):
    owner: Address
    guidelines: str           # the written code of conduct
    posts: TreeMap[str, Post]
    reputation: TreeMap[Address, i32]

    def __init__(self, guidelines: str):
        self.owner = gl.message.sender_address
        self.guidelines = guidelines

    @gl.public.write
    def set_guidelines(self, guidelines: str):
        assert gl.message.sender_address == self.owner, "only owner"
        self.guidelines = guidelines

    @gl.public.view
    def get_post(self, post_id: str) -> dict:
        p = self.posts[post_id]
        return {
            "id": p.id,
            "author": str(p.author),
            "content_url": p.content_url,
            "content_snapshot": p.content_snapshot,
            "verdict": p.verdict,
            "severity": int(p.severity),
            "violated_rules": p.violated_rules,
            "rationale": p.rationale,
            "finalized": p.finalized,
            "appealed": p.appealed,
        }

    @gl.public.view
    def reputation_of(self, addr: Address) -> int:
        return int(self.reputation.get(addr, i32(0)))

    @gl.public.write
    def moderate(self, post_id: str, author: Address, content_url: str):
        """
        Pull `content_url`, classify it under the current guidelines, and
        write the verdict + reputation delta if validators accept.
        """
        assert post_id not in self.posts, "already moderated"
        guidelines = self.guidelines

        def leader_fn():
            page = gl.nondet.web.get(content_url)
            content = page.body[:4000]
            prompt = f"""
You are a strict but fair content moderator.

Community guidelines:
---
{guidelines}
---

Submitted content (truncated):
---
{content}
---

Decide the action. Reply with JSON only:
{{
  "verdict": "ok" | "warn" | "remove" | "ban",
  "severity": <integer 0..10>,
  "violated_rules": ["<rule id>", ...],
  "rationale": "<2-3 sentence explanation grounded in the guidelines>",
  "content_snapshot": "<<=240 chars verbatim quote of the worst offending substring, or empty>"
}}
""".strip()
            return json.loads(gl.nondet.exec_prompt(prompt))

        def validator_fn(leader_result) -> bool:
            if not isinstance(leader_result, gl.vm.Return):
                return False
            try:
                page = gl.nondet.web.get(content_url)
                content = page.body[:4000]
            except Exception:
                return False

            leader_json = json.dumps(leader_result.calldata, sort_keys=True)

            # Ask the validator's LLM: is the leader's verdict defensible
            # against this exact source content and these exact guidelines?
            verdict = gl_call.gl_call_generic(
                {
                    "ExecPromptTemplate": {
                        "template": "EqNonComparativeValidator",
                        "input": f"GUIDELINES:\n{guidelines}\n\nCONTENT:\n{content}",
                        "leader_answer": leader_json,
                        "task": "Classify the content against the guidelines and choose verdict in {ok, warn, remove, ban}.",
                        "criteria": (
                            "Verdict must be a defensible reading of the guidelines given the content. "
                            "Severity must be within ±2 of what the content warrants. "
                            "Every entry in violated_rules must be a real rule id from the guidelines. "
                            "Rationale must cite the same rule(s) as violated_rules. "
                            "content_snapshot, if non-empty, must appear verbatim in CONTENT."
                        ),
                    }
                },
                _decode_nondet,
            ).get()
            return bool(verdict)

        result = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)

        post = Post()
        post.id = post_id
        post.author = author
        post.content_url = content_url
        post.content_snapshot = str(result.get("content_snapshot", ""))[:240]
        post.verdict = str(result["verdict"])
        post.severity = u8(int(result.get("severity", 0)))
        post.violated_rules = ",".join(result.get("violated_rules", []))
        post.rationale = str(result["rationale"])
        post.finalized = True
        post.appealed = False
        self.posts[post_id] = post

        delta = VERDICT_REP_DELTA.get(post.verdict, 0)
        if delta != 0:
            cur = int(self.reputation.get(author, i32(0)))
            self.reputation[author] = i32(cur + delta)

    @gl.public.write
    def appeal(self, post_id: str):
        """
        Author can appeal a non-`ok` verdict once. The contract re-runs the
        same equivalence block with `appeal_mode=True` baked into the prompt;
        if the new verdict is more lenient, reputation is restored
        proportionally.
        """
        post = self.posts[post_id]
        assert gl.message.sender_address == post.author, "only author"
        assert not post.appealed, "already appealed"
        assert post.verdict != VERDICT_OK, "nothing to appeal"
        guidelines = self.guidelines
        snapshot = post.content_snapshot
        prior = post.verdict

        def leader_fn():
            prompt = f"""
You are reviewing an appeal of a prior moderation decision.

Community guidelines:
---
{guidelines}
---

Prior verdict: {prior}
Excerpt that was flagged:
---
{snapshot}
---

Take the most lenient defensible position. Reply with JSON only:
{{ "verdict": "ok" | "warn" | "remove" | "ban", "rationale": "<one paragraph>" }}
""".strip()
            return json.loads(gl.nondet.exec_prompt(prompt))

        def validator_fn(leader_result) -> bool:
            if not isinstance(leader_result, gl.vm.Return):
                return False
            v = leader_fn()
            l = leader_result.calldata
            # Validators must agree on the appeal verdict exactly.
            return str(l["verdict"]) == str(v["verdict"])

        result = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)
        new_verdict = str(result["verdict"])
        post.appealed = True

        if new_verdict != prior:
            # Reverse the original delta, apply the new (lighter) one.
            old_delta = VERDICT_REP_DELTA.get(prior, 0)
            new_delta = VERDICT_REP_DELTA.get(new_verdict, 0)
            cur = int(self.reputation.get(post.author, i32(0)))
            self.reputation[post.author] = i32(cur - old_delta + new_delta)
            post.verdict = new_verdict
            post.rationale = f"[APPEAL UPHELD] {result['rationale']}"
        else:
            post.rationale = f"[APPEAL DENIED] {result['rationale']}"

        self.posts[post_id] = post
