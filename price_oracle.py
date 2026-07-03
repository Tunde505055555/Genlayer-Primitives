# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
"""
AI Web Oracle
=============

Pulls a price quote from a public web page, asks an LLM to extract a
normalized {price, currency, confidence} record, and reaches consensus by
comparing the decision fields with numeric tolerance (±2%).

Consensus pattern: Numeric Tolerance + Partial Field Matching.
The validator re-runs the same task; the leader is accepted iff
  - currency matches exactly
  - price is within ±2% of the validator's own price
  - confidence is within ±1 (0..10)
  - reject sentinel (confidence == 0) is unanimous
"""

from genlayer import *

import json
import typing


PRICE_TOLERANCE_BPS = 200  # 2.00 %


class AIWebOracle(gl.Contract):
    owner: Address
    pair: str
    source_url: str
    price_micro: u256      # price * 1_000_000
    currency: str
    confidence: u256       # 0..10
    analysis: str
    updated_at_block: u256

    def __init__(self, pair: str, source_url: str):
        self.owner = gl.message.sender_address
        self.pair = pair
        self.source_url = source_url
        self.price_micro = u256(0)
        self.currency = ""
        self.confidence = u256(0)
        self.analysis = ""
        self.updated_at_block = u256(0)

    @gl.public.view
    def get_price(self) -> dict:
        return {
            "pair": self.pair,
            "source_url": self.source_url,
            "price_micro": int(self.price_micro),
            "currency": self.currency,
            "confidence": int(self.confidence),
            "analysis": self.analysis,
            "updated_at_block": int(self.updated_at_block),
        }

    @gl.public.write
    def set_source(self, pair: str, source_url: str) -> None:
        if gl.message.sender_address != self.owner:
            raise gl.vm.UserError("only owner")
        self.pair = pair
        self.source_url = source_url

    @gl.public.write
    def refresh(self) -> None:
        source_url = self.source_url
        pair = self.pair

        def fetch_and_extract() -> typing.Any:
            page = gl.nondet.web.render(source_url, mode="text")
            body = page[:2000]
            task = f"""
You are a deterministic financial extractor.

Trading pair: {pair}
Source URL: {source_url}
Page content (truncated):
---
{body}
---

Extract the current mid price. Reply with ONLY a JSON object of the form:
{{
  "price": <number>,
  "currency": "<ISO ticker like USD>",
  "confidence": <integer 0..10, 0 means no price visible>,
  "analysis": "<one short sentence>"
}}
""".strip()
            raw = gl.nondet.exec_prompt(task)
            raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            data = json.loads(raw)
            return {
                "price_micro": int(round(float(data["price"]) * 1_000_000)),
                "currency": str(data["currency"]).upper(),
                "confidence": int(data["confidence"]),
                "analysis": str(data.get("analysis", ""))[:240],
            }

        def equivalence(a: typing.Any, b: typing.Any) -> bool:
            if a["currency"] != b["currency"]:
                return False
            ac, bc = int(a["confidence"]), int(b["confidence"])
            if ac == 0 or bc == 0:
                return ac == 0 and bc == 0
            if abs(ac - bc) > 1:
                return False
            ap, bp = int(a["price_micro"]), int(b["price_micro"])
            if ap == 0:
                return bp == 0
            drift_bps = abs(ap - bp) * 10_000 // abs(ap)
            return drift_bps <= PRICE_TOLERANCE_BPS

        result = gl.eq_principle.strict_eq(fetch_and_extract) if False else gl.eq_principle.prompt_comparative(
            fetch_and_extract,
            "Two JSON records are equivalent iff currency matches exactly, price is within 2%, and confidence is within 1 (0..10). If either side reports confidence=0, both must.",
        )

        if int(result["confidence"]) == 0:
            raise gl.vm.UserError(f"oracle rejected: no price visible for {pair}")

        self.price_micro = u256(int(result["price_micro"]))
        self.currency = str(result["currency"])
        self.confidence = u256(int(result["confidence"]))
        self.analysis = str(result["analysis"])
