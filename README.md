# GenLayer Contract Primitives

Three production-ready GenLayer Intelligent Contract primitives that demonstrate
distinct consensus patterns under the **Equivalence Principle**.

Each contract is standalone, documented inline, and paired with an interactive
simulation in the companion web playground.

| Contract | File | Consensus pattern |
| --- | --- | --- |
| AI Web Oracle | [`price_oracle.py`](./price_oracle.py) | Numeric tolerance + partial-field matching |
| Content Moderation | [`content_moderation.py`](./content_moderation.py) | Source-grounded non-comparative validation |
| AI Arbitration Escrow | [`arbitration_escrow.py`](./arbitration_escrow.py) | Comparative LLM judgment + decision-field matching |

## The Equivalence Principle in one paragraph

GenLayer validators must reach consensus on **non-deterministic** results
(web fetches, LLM outputs). A randomly-selected *leader* executes the
non-deterministic block and publishes a result. Every *validator* independently
re-executes the block (or re-evaluates the leader's output against the same
source) and votes accept or reject. The leader's result is canonicalized into
on-chain state only if a majority agrees. If consensus fails, the network
rotates the leader; if it still fails, the transaction goes **undetermined**
and state is unchanged.

The hard part is the *validator function*: it must verify the substance of the
leader's answer, not just its shape. The three patterns below show how.

## Pattern 1 — Numeric Tolerance + Partial Fields (`price_oracle.py`)

Use when the leader returns structured data with both subjective fields
(reasoning, analysis text) and objective decision fields (prices, scores,
enums), and where the objective fields may drift slightly between runs.

- **Leader**: scrapes a web page, asks the LLM to extract a price and a
  confidence score, returns `{price, currency, confidence, analysis, source_excerpt}`.
- **Validator**: re-runs the same task, then enforces:
  1. `currency` matches exactly,
  2. `price` is within ±2 % of the leader's price,
  3. `confidence` is within ±1 (0–10 scale),
  4. both scores agree if either is the reject sentinel (`0`).
- Subjective `analysis` and `source_excerpt` are stored but never compared —
  two LLMs will always word their reasoning differently.

## Pattern 2 — Source-Grounded Non-Comparative (`content_moderation.py`)

Use when "is this acceptable?" is a judgment call that should be evaluated
against explicit, written criteria — not by independently generating a second
opinion and comparing.

- **Leader**: pulls the submitted post, asks the LLM to classify it against the
  community guidelines, returns `{verdict, severity, violated_rules, rationale}`.
- **Validator**: re-fetches the same content, then uses the
  `EqNonComparativeValidator` template to judge whether the leader's verdict
  is *defensible* given the source and the criteria. The validator does **not**
  produce its own verdict — it grades the leader's.
- This is the right pattern for moderation, code review, and any task where
  multiple defensible answers exist but only one needs to be picked.

## Pattern 3 — Comparative LLM Judgment (`arbitration_escrow.py`)

Use when the leader produces a rich, structured ruling (a decision plus
multi-paragraph reasoning) and what matters is that the *decision* matches
and the *reasoning* is materially equivalent, even though no two LLMs will
write it the same way.

- **Leader**: reads both parties' statements + evidence URLs, returns
  `{ruling, awarded_to, refund_bps, key_facts[], reasoning}`.
- **Validator**: re-runs the analysis, then invokes the `EqComparative`
  template with a `principle` that pins down what must match (the ruling
  enum, the awarded party, the refund within ±100 bps) and what may differ
  (the prose reasoning, the ordering of `key_facts`).
- The contract then settles the escrow on-chain based on the agreed ruling.

**Studio deploy note:** deploy `Escrow` with a payee as a `0x...` address
string and the agreement text, then call `fund()` with GEN value. The
constructor intentionally does not receive GEN value because value is only
available to payable write methods.

## Why these three together

| Axis | Oracle | Moderation | Arbitration |
| --- | --- | --- | --- |
| Validator generates own answer? | Yes | No | Yes |
| Comparison mechanism | Programmatic (numeric) | LLM judges leader vs criteria | LLM judges leader vs validator |
| Subjective fields | Stored, not compared | Stored, not compared | Compared via principle |
| On-chain side effects | Update price table | Apply moderation action | Move escrowed funds |

Together they cover the three places real GenLayer apps end up: feeding the
chain with external data, gating user-generated content, and resolving
disputes between parties.

## Running the contracts

Deploy any of these in the [GenLayer Studio](https://studio.genlayer.com/),
or via the [GenLayer CLI](https://docs.genlayer.com/developers/genlayer-js)
against a localnet:

```bash
genlayer deploy contracts/price_oracle.py \
  --args '["BTC-USD", "https://api.example.com/prices/BTC-USD"]'
```

See [`tests/`](./tests) for the equivalence-principle test sketches that
exercise the leader/validator branches in isolation.

### Arbitration escrow Studio flow

1. Deploy `arbitration_escrow.py` with:
   - `payee`: a 0x-prefixed 20-byte address string
   - `agreement`: the written deal or milestone terms
2. Call `fund()` from the payer account and send the escrow amount as GEN value.
3. If there is no dispute, the payer can call `release()` or the payee can call
   `refund()`.
4. If there is a dispute, either party calls `open_dispute(...)`, then anyone
   can call `settle()` to run AI arbitration and split the escrowed value.

## License

MIT — primitives are intended to be copied, modified, and shipped.
