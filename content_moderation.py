# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
"""
Content Moderation with Reputation
==================================

Subjective moderation queue. Anyone submits a post with a URL to the
content and community guidelines are held on-chain. The leader classifies
the post; validators judge whether the leader's verdict is defensible
against the same source and the same written criteria (non-comparative
equivalence). Reputation moves as a side-effect of accepted verdicts.
"""

from genlayer import *

import json
import typing


VERDICTS = ("ok", "warn", "remove", "ban")
VERDICT_DELTA = {"ok": 1, "warn": -2, "remove": -10, "ban": -50}


class ContentModeration(gl.Contract):
    owner: Address
    guidelines: str
    # Flat storage: one TreeMap per field, keyed by post_id.
    post_authors: TreeMap[str, Address]
    post_urls: TreeMap[str, str]
    post_verdicts: TreeMap[str, str]
    post_rationales: TreeMap[str, str]
    reputation: TreeMap[Address, u256]  # stored as offset from 1_000_000 (avoid signed)

    REP_ZERO: typing.ClassVar[int] = 1_000_000

    def __init__(self, guidelines: str):
        self.owner = gl.message.sender_address
        self.guidelines = guidelines

    @gl.public.write
    def set_guidelines(self, guidelines: str) -> None:
        if gl.message.sender_address != self.owner:
            raise gl.vm.UserError("only owner")
        self.guidelines = guidelines

    @gl.public.view
    def get_post(self, post_id: str) -> dict:
        return {
            "id": post_id,
            "author": str(self.post_authors[post_id]),
            "content_url": self.post_urls[post_id],
            "verdict": self.post_verdicts[post_id],
            "rationale": self.post_rationales[post_id],
        }

    @gl.public.view
    def reputation_of(self, addr: Address) -> int:
        cur = int(self.reputation.get(addr, u256(self.REP_ZERO)))
        return cur - self.REP_ZERO

    @gl.public.write
    def moderate(self, post_id: str, author: Address, content_url: str) -> None:
        if post_id in self.post_authors:
            raise gl.vm.UserError("already moderated")
        guidelines = self.guidelines

        def classify() -> typing.Any:
            page = gl.nondet.web.render(content_url, mode="text")
            content = page[:3500]
            task = f"""
You are a strict but fair content moderator.

Community guidelines:
---
{guidelines}
---

Submitted content (truncated):
---
{content}
---

Decide the action. Reply with ONLY a JSON object:
{{
  "verdict": "ok" | "warn" | "remove" | "ban",
  "rationale": "<2-3 sentences citing rule ids>"
}}
""".strip()
            raw = gl.nondet.exec_prompt(task)
            raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            data = json.loads(raw)
            v = str(data["verdict"]).lower()
            if v not in VERDICTS:
                raise gl.vm.Rollback(f"invalid verdict: {v}")
            return {"verdict": v, "rationale": str(data.get("rationale", ""))[:600]}

        result = gl.eq_principle.prompt_non_comparative(
            classify,
            task="Classify the submitted content against the community guidelines.",
            criteria=(
                "The verdict must be a defensible reading of the guidelines given the content. "
                "The rationale must cite the guideline rule(s) actually violated. "
                "The verdict must be one of: ok, warn, remove, ban."
            ),
        )

        verdict = str(result["verdict"]).lower()
        self.post_authors[post_id] = author
        self.post_urls[post_id] = content_url
        self.post_verdicts[post_id] = verdict
        self.post_rationales[post_id] = str(result["rationale"])

        delta = VERDICT_DELTA.get(verdict, 0)
        if delta != 0:
            cur = int(self.reputation.get(author, u256(self.REP_ZERO)))
            self.reputation[author] = u256(max(0, cur + delta))
