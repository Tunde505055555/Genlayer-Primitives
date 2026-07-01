# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
"""
AI Web Oracle
=============

Pulls a price quote (or any numeric metric) from a public web page, asks an
LLM to extract a normalized {price, currency, confidence, analysis} record,
and reaches consensus by comparing the *decision fields* with numeric
tolerance.

Consensus pattern
-----------------
Pattern: Numeric Tolerance + Partial Field Matching.

The leader's price will almost never be bit-exact across nodes (the upstream
quote drifts between fetches, and the LLM extraction adds tiny variance).
The validator re-runs the same task and accepts the leader if:

  - `currency` matches exactly
  - `price` is within ±2 % of the validator's own price
  - `confidence` is within ±1 (0..10), or both scored 0 (reject)

`analysis` and `source_excerpt` are stored but never compared. Two LLMs
will always word them differently.

Why this is useful
------------------
This is the smallest standalone primitive that turns *any* web page with a
visible quote (CEX, DEX, NOAA weather feed, sports scoreboard, government
yield curve) into a trust-minimized on-chain feed without needing the
publisher to sign anything. Drop in a different `prompt_template` and you
have a different oracle.
"""

from genlayer import *
import json


class PriceFeed:
    pair: str
    source_url: str
    price: u256          # stored as integer micro-units (price * 1e6)
    confidence: u8       # 0..10
    analysis: str
    source_excerpt: str
    updated_at_block: u64


# Reject sentinel — leader uses confidence=0 to say "do not update".
REJECT = 0
# Tolerance the validator allows around the leader's price.
PRICE_TOLERANCE_BPS = 200  # 2.00 %


class AIWebOracle(gl.Contract):
    owner: Address
    feeds: TreeMap[str, PriceFeed]

    def __init__(self):
        self.owner = gl.message.sender_address

    # --- admin -------------------------------------------------------------

    @gl.public.write
    def register_feed(self, pair: str, source_url: str):
        assert gl.message.sender_address == self.owner, "only owner"
        assert pair not in self.feeds, "feed exists"
        feed = PriceFeed()
        feed.pair = pair
        feed.source_url = source_url
        feed.price = u256(0)
        feed.confidence = u8(0)
        feed.analysis = ""
        feed.source_excerpt = ""
        feed.updated_at_block = u64(0)
        self.feeds[pair] = feed

    # --- reads -------------------------------------------------------------

    @gl.public.view
    def get_price(self, pair: str) -> dict:
        f = self.feeds[pair]
        return {
            "pair": f.pair,
            "price_micro": int(f.price),
            "confidence": int(f.confidence),
            "analysis": f.analysis,
            "source_excerpt": f.source_excerpt,
            "updated_at_block": int(f.updated_at_block),
        }

    # --- write under consensus --------------------------------------------

    @gl.public.write
    def refresh(self, pair: str):
        """
        Refresh `pair` by running the leader/validator block. State only
        updates if the validator quorum accepts the leader's result.
        """
        feed = self.feeds[pair]
        source_url = feed.source_url

        def leader_fn():
            page = gl.nondet.web.get(source_url)
            body_excerpt = page.body[:2000]
            prompt = f"""
You are a deterministic financial extractor.

Source URL: {source_url}
Trading pair: {pair}
Page content (truncated):
---
{body_excerpt}
---

Extract the current mid price for the pair above. Reply with JSON only:
{{
  "price": <number, in the natural unit of the quote currency>,
  "currency": "<ISO-style ticker, e.g. USD>",
  "confidence": <integer 0..10, where 0 means 'no price visible, reject'>,
  "analysis": "<one short sentence about where on the page you read the price>",
  "source_excerpt": "<the exact substring you used, <= 120 chars>"
}}
""".strip()
            response = gl.nondet.exec_prompt(prompt)
            data = json.loads(response)
            # Normalize to micro-units so we can store as u256.
            data["price_micro"] = int(round(float(data["price"]) * 1_000_000))
            return data

        def validator_fn(leader_result) -> bool:
            if not isinstance(leader_result, gl.vm.Return):
                return False
            try:
                v = leader_fn()
                l = leader_result.calldata
            except Exception:
                return False

            # 1. Currency must match exactly.
            if str(l.get("currency", "")).upper() != str(v.get("currency", "")).upper():
                return False

            # 2. Reject sentinel: if either side says "no price visible",
            #    both must agree, otherwise we'd let an LLM hallucination
            #    upgrade a rejection into an accepted price.
            l_conf, v_conf = int(l["confidence"]), int(v["confidence"])
            if l_conf == REJECT or v_conf == REJECT:
                return l_conf == REJECT and v_conf == REJECT

            # 3. Confidence within ±1 on 0..10 scale.
            if abs(l_conf - v_conf) > 1:
                return False

            # 4. Price within ±PRICE_TOLERANCE_BPS.
            lp, vp = int(l["price_micro"]), int(v["price_micro"])
            if lp == 0:
                return vp == 0
            drift_bps = abs(lp - vp) * 10_000 // abs(lp)
            return drift_bps <= PRICE_TOLERANCE_BPS

        result = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)

        if int(result["confidence"]) == REJECT:
            raise gl.vm.UserError(f"oracle rejected: no price visible for {pair}")

        feed.price = u256(int(result["price_micro"]))
        feed.confidence = u8(int(result["confidence"]))
        feed.analysis = str(result["analysis"])
        feed.source_excerpt = str(result["source_excerpt"])[:240]
        feed.updated_at_block = u64(int(gl.message.block_number))
        self.feeds[pair] = feed
