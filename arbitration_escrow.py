# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
"""
AI Arbitration Escrow
=====================

Two-party escrow with AI-judged dispute resolution and deterministic
on-chain settlement.

Consensus pattern: Comparative LLM Judgment + Decision-Field Matching.
Validators re-run the arbitration; the leader is accepted iff the objective
settlement fields (ruling, awarded_to, refund_bps ±100) match exactly and
the reasoning prose is judged equivalent under the stated principle.
"""

from genlayer import *

import json
import typing


STATE_FUNDED = "funded"
STATE_AWAITING_FUNDS = "awaiting_funds"
STATE_RELEASED = "released"
STATE_REFUNDED = "refunded"
STATE_DISPUTED = "disputed"
STATE_SETTLED = "settled"


@gl.evm.contract_interface
class _Recipient:
    class View:
        pass

    class Write:
        pass


def _parse_address(value: str, label: str) -> Address:
    try:
        return Address(value)
    except Exception:
        raise gl.vm.UserError(f"{label} must be a 0x-prefixed 20-byte address string")


def _send_gen(to: Address, amount: int) -> None:
    if amount > 0:
        _Recipient(to).emit_transfer(value=u256(amount))


class Escrow(gl.Contract):
    payer: Address
    payee: Address
    amount: u256
    agreement: str
    state: str
    payer_statement: str
    payer_evidence: str
    payee_statement: str
    payee_evidence: str
    ruling: str
    refund_bps: u256
    reasoning: str

    def __init__(self, payee: str, agreement: str):
        """
        Create an unfunded escrow.

        Studio deploy forms pass address inputs most reliably as strings, so
        `payee` is accepted as a 0x-prefixed address string and converted to
        GenLayer's Address type before it is stored. After deploy, the payer
        calls fund() with GEN value to lock the escrow amount.
        """
        self.payer = gl.message.sender_address
        self.payee = _parse_address(payee, "payee")
        self.amount = u256(0)
        self.agreement = agreement
        self.state = STATE_AWAITING_FUNDS
        self.payer_statement = ""
        self.payer_evidence = ""
        self.payee_statement = ""
        self.payee_evidence = ""
        self.ruling = ""
        self.refund_bps = u256(0)
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
            "reasoning": self.reasoning,
        }

    @gl.public.write.payable
    def fund(self) -> None:
        if gl.message.sender_address != self.payer:
            raise gl.vm.UserError("only payer")
        if self.state != STATE_AWAITING_FUNDS:
            raise gl.vm.UserError("already funded")
        value = gl.message.value
        if int(value) == 0:
            raise gl.vm.UserError("send escrow value")
        self.amount = u256(int(value))
        self.state = STATE_FUNDED

    @gl.public.write
    def release(self) -> None:
        if gl.message.sender_address != self.payer:
            raise gl.vm.UserError("only payer")
        if self.state != STATE_FUNDED:
            raise gl.vm.UserError("not releasable")
        _send_gen(self.payee, int(self.amount))
        self.state = STATE_RELEASED

    @gl.public.write
    def refund(self) -> None:
        if gl.message.sender_address != self.payee:
            raise gl.vm.UserError("only payee")
        if self.state != STATE_FUNDED:
            raise gl.vm.UserError("not refundable")
        _send_gen(self.payer, int(self.amount))
        self.state = STATE_REFUNDED

    @gl.public.write
    def open_dispute(
        self,
        payer_statement: str,
        payer_evidence: str,
        payee_statement: str,
        payee_evidence: str,
    ) -> None:
        sender = gl.message.sender_address
        if sender != self.payer and sender != self.payee:
            raise gl.vm.UserError("not a party")
        if self.state != STATE_FUNDED:
            raise gl.vm.UserError("wrong state")
        self.payer_statement = payer_statement
        self.payer_evidence = payer_evidence
        self.payee_statement = payee_statement
        self.payee_evidence = payee_evidence
        self.state = STATE_DISPUTED

    @gl.public.write
    def settle(self) -> None:
        if self.state != STATE_DISPUTED:
            raise gl.vm.UserError("no open dispute")

        agreement = self.agreement
        payer_stmt = self.payer_statement
        payer_ev = self.payer_evidence
        payee_stmt = self.payee_statement
        payee_ev = self.payee_evidence

        def arbitrate() -> typing.Any:
            def fetch_all(urls_blob: str) -> str:
                parts = []
                for url in [u.strip() for u in urls_blob.splitlines() if u.strip()]:
                    try:
                        body = gl.nondet.web.render(url, mode="text")
                        parts.append(f"--- {url} ---\n{body[:1200]}")
                    except Exception as e:
                        parts.append(f"--- {url} ---\n[fetch failed: {e}]")
                return "\n\n".join(parts)

            payer_ev_body = fetch_all(payer_ev)
            payee_ev_body = fetch_all(payee_ev)

            task = f"""
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

Reply with ONLY a JSON object:
{{
  "ruling": "payee" | "payer" | "split",
  "refund_bps": <integer 0..10000, share returned to PAYER>,
  "reasoning": "<one paragraph applying the agreement to the facts>"
}}

Rules: refund_bps must be 0 when ruling=="payee", 10000 when
ruling=="payer", and strictly between 1 and 9999 when ruling=="split".
""".strip()
            raw = gl.nondet.exec_prompt(task)
            raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            data = json.loads(raw)
            ruling = str(data["ruling"]).lower()
            bps = int(data["refund_bps"])
            if ruling not in ("payee", "payer", "split"):
                raise gl.vm.Rollback(f"invalid ruling: {ruling}")
            if not (0 <= bps <= 10000):
                raise gl.vm.Rollback(f"refund_bps out of range: {bps}")
            return {
                "ruling": ruling,
                "refund_bps": bps,
                "reasoning": str(data.get("reasoning", ""))[:1000],
            }

        result = gl.eq_principle.prompt_comparative(
            arbitrate,
            "The `ruling` field must match exactly. `refund_bps` must be within 100 basis points. "
            "The `reasoning` may be worded differently but must rely on the same agreement clauses "
            "and reach the same conclusion.",
        )

        self.ruling = str(result["ruling"])
        self.refund_bps = u256(int(result["refund_bps"]))
        self.reasoning = str(result["reasoning"])

        total = int(self.amount)
        to_payer = total * int(self.refund_bps) // 10000
        to_payee = total - to_payer
        _send_gen(self.payer, to_payer)
        _send_gen(self.payee, to_payee)
        self.state = STATE_SETTLED
