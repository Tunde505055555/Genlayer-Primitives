# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
"""
AI Arbitration Escrow
=====================

Two parties — a `payer` and a `payee` — lock funds against a written
agreement. Either party can release on success. If they disagree, either
side can `dispute()`, attaching their statement and evidence URLs. The
contract reaches consensus on a structured ruling and settles funds
automatically.

Consensus pattern
-----------------
Pattern: Comparative LLM Judgment + Decision Fields.

A dispute ruling has both:

  - **objective** fields that must match (ruling enum, awarded party,
    refund amount within ±100 bps),
  - **subjective** fields (the prose reasoning, the ordering of key facts)
    where two LLMs will never produce identical text but must produce
    *materially equivalent* arguments.

The validator re-runs the analysis, then invokes the `EqComparative`
template with a `principle` that pins down what must match and explicitly
permits the rest to differ. This combines deterministic gating on the
settlement fields with LLM-based equivalence on the reasoning.

Why this is useful
------------------
- Drop-in escrow for freelance work, marketplace transactions, hackathon
  prize pools, or any two-party agreement with off-chain evidence.
- All settlement math is on-chain and deterministic; only the *ruling* is
  AI-derived, and it goes through full consensus.
- Evidence URLs become part of permanent, queryable contract state.
"""

from genlayer import *
import json
import genlayer.gl._internal.gl_call as gl_call
from genlayer.gl.nondet import _decode_nondet


STATE_FUNDED = "funded"
STATE_RELEASED = "released"
STATE_REFUNDED = "refunded"
STATE_DISPUTED = "disputed"
STATE_SETTLED = "settled"

RULING_PAYEE = "payee"
RULING_PAYER = "payer"
RULING_SPLIT = "split"


class Escrow(gl.Contract):
    payer: Address
    payee: Address
    amount: u256
    agreement: str
    state: str
    # Dispute material:
    payer_statement: str
    payer_evidence: str
    payee_statement: str
    payee_evidence: str
    # Final ruling:
    ruling: str
    refund_bps: u32        # 0..10000, share returned to payer
    key_facts: str         # newline-joined
    reasoning: str

    def __init__(self, payee: Address, agreement: str):
        self.payer = gl.message.sender_address
        self.payee = payee
        self.amount = u256(int(gl.message.value))
        assert int(self.amount) > 0, "escrow must be funded"
        self.agreement = agreement
        self.state = STATE_FUNDED
        self.payer_statement = ""
        self.payer_evidence = ""
        self.payee_statement = ""
        self.payee_evidence = ""
        self.ruling = ""
        self.refund_bps = u32(0)
        self.key_facts = ""
        self.reasoning = ""

    @gl.public.view
    def summary(self) -> dict:
        return {
            "payer": str(self.payer),
            "payee": str(self.payee),
            "amount": int(self.amount),
            "agreement": self.agreement,
            "state": self.state,
            "ruling": self.ruling,
            "refund_bps": int(self.refund_bps),
            "key_facts": self.key_facts.split("\n") if self.key_facts else [],
            "reasoning": self.reasoning,
        }

    @gl.public.write.payable
    def release(self):
        """Payer voluntarily releases funds to payee."""
        assert gl.message.sender_address == self.payer, "only payer"
        assert self.state == STATE_FUNDED, "not releasable"
        gl.message.send(self.payee, int(self.amount))
        self.state = STATE_RELEASED

    @gl.public.write
    def refund(self):
        """Payee voluntarily refunds the payer."""
        assert gl.message.sender_address == self.payee, "only payee"
        assert self.state == STATE_FUNDED, "not refundable"
        gl.message.send(self.payer, int(self.amount))
        self.state = STATE_REFUNDED

    @gl.public.write
    def open_dispute(
        self,
        payer_statement: str,
        payer_evidence: str,
        payee_statement: str,
        payee_evidence: str,
    ):
        """Either party opens a dispute with both sides' material."""
        sender = gl.message.sender_address
        assert sender == self.payer or sender == self.payee, "not a party"
        assert self.state == STATE_FUNDED, "wrong state"
        self.payer_statement = payer_statement
        self.payer_evidence = payer_evidence
        self.payee_statement = payee_statement
        self.payee_evidence = payee_evidence
        self.state = STATE_DISPUTED

    @gl.public.write
    def settle(self):
        """
        Anyone can trigger settlement once disputed. The leader produces a
        ruling under the equivalence principle; once validators accept,
        funds move on-chain deterministically.
        """
        assert self.state == STATE_DISPUTED, "no open dispute"

        agreement = self.agreement
        payer_stmt = self.payer_statement
        payer_ev = self.payer_evidence
        payee_stmt = self.payee_statement
        payee_ev = self.payee_evidence

        def leader_fn():
            # Pull each party's evidence URL list (newline-separated).
            def fetch_all(urls_blob: str) -> str:
                out = []
                for url in [u.strip() for u in urls_blob.splitlines() if u.strip()]:
                    try:
                        page = gl.nondet.web.get(url)
                        out.append(f"--- {url} ---\n{page.body[:1500]}")
                    except Exception as e:
                        out.append(f"--- {url} ---\n[fetch failed: {e}]")
                return "\n\n".join(out)

            payer_ev_body = fetch_all(payer_ev)
            payee_ev_body = fetch_all(payee_ev)

            prompt = f"""
You are an impartial arbitrator. Apply the agreement literally; resolve
ambiguity in favor of the party with stronger evidence.

AGREEMENT
---
{agreement}
---

PAYER STATEMENT
---
{payer_stmt}
---
PAYER EVIDENCE
---
{payer_ev_body}
---

PAYEE STATEMENT
---
{payee_stmt}
---
PAYEE EVIDENCE
---
{payee_ev_body}
---

Reply with JSON only:
{{
  "ruling": "payee" | "payer" | "split",
  "awarded_to": "payee" | "payer" | "both",
  "refund_bps": <integer 0..10000, share returned to PAYER>,
  "key_facts": ["<short factual finding>", ...],
  "reasoning": "<one paragraph applying the agreement to the facts>"
}}

Rules: refund_bps must be 0 when ruling==payee, 10000 when ruling==payer,
strictly between 1 and 9999 when ruling==split.
""".strip()
            return json.loads(gl.nondet.exec_prompt(prompt))

        def validator_fn(leader_result) -> bool:
            if not isinstance(leader_result, gl.vm.Return):
                return False
            try:
                v = leader_fn()
                l = leader_result.calldata
            except Exception:
                return False

            # Hard deterministic gates on the settlement fields.
            if str(l["ruling"]) != str(v["ruling"]):
                return False
            if str(l["awarded_to"]) != str(v["awarded_to"]):
                return False
            if abs(int(l["refund_bps"]) - int(v["refund_bps"])) > 100:
                return False
            # Internal consistency.
            if l["ruling"] == RULING_PAYEE and int(l["refund_bps"]) != 0:
                return False
            if l["ruling"] == RULING_PAYER and int(l["refund_bps"]) != 10000:
                return False
            if l["ruling"] == RULING_SPLIT and not (0 < int(l["refund_bps"]) < 10000):
                return False

            # Soft equivalence on the reasoning prose via EqComparative.
            verdict = gl_call.gl_call_generic(
                {
                    "ExecPromptTemplate": {
                        "template": "EqComparative",
                        "leader_answer": json.dumps(l, sort_keys=True),
                        "validator_answer": json.dumps(v, sort_keys=True),
                        "principle": (
                            "`ruling`, `awarded_to`, and `refund_bps` (±100) must match. "
                            "`key_facts` must overlap substantially (same core findings, ordering may differ). "
                            "`reasoning` may be worded differently but must rely on the same agreement clauses "
                            "and reach the same conclusion. Stylistic differences are fine."
                        ),
                    }
                },
                _decode_nondet,
            ).get()
            return bool(verdict)

        result = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)

        self.ruling = str(result["ruling"])
        self.refund_bps = u32(int(result["refund_bps"]))
        self.key_facts = "\n".join(result.get("key_facts", []))
        self.reasoning = str(result["reasoning"])

        total = int(self.amount)
        to_payer = total * int(self.refund_bps) // 10000
        to_payee = total - to_payer
        if to_payer > 0:
            gl.message.send(self.payer, to_payer)
        if to_payee > 0:
            gl.message.send(self.payee, to_payee)
        self.state = STATE_SETTLED
