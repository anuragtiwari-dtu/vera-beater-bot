# Vera-beater — submission README

## Approach

A **deterministic, rule-based composer** rather than an LLM call. `composer.py`
has one hand-written handler per trigger `kind` (18 of the 26 kinds get a
dedicated handler; the remaining 8 customer-scope kinds share one well-tuned
fallback; anything unrecognised falls back to a signal-driven generic). Every
handler reads directly from the `category` / `merchant` / `trigger` /
`customer` dicts it's given — never from an LLM's imagination — so specificity
and "don't fabricate" (brief §5.8) are structural guarantees, not prompt
instructions.

Key design choice: **most generated triggers ship with `{"placeholder": true}`
payloads** (only the seed triggers carry rich payloads). When that happens the
composer doesn't invent detail — it falls back to a *real* number computed
from the merchant/customer context instead:
- `dormant_with_vera` → actual days since the last `conversation_history`
  entry (computed against the tick's own `now`, not guessed)
- `perf_spike` / `perf_dip` → the merchant's own `delta_7d` numbers
- `recall_due` / `customer_lapsed_soft` / `chronic_refill_due` /
  `trial_followup` → days since the customer's real `last_visit`
- `milestone_reached` → real `customer_aggregate.total_unique_ytd` or
  `performance.views`
- `competitor_opened` → peer-benchmark comparison (never a fabricated
  competitor name, since none was ever supplied)

Every composed message also runs through shared scaffolding for: category
voice/salutation match, Hindi-English code-mix (only when
`merchant.identity.languages` includes `hi` **and** the category's `code_mix`
allows it), single trailing CTA (binary YES/STOP for action triggers,
open-ended for informational ones, none for pure FYI), and correct
`send_as` (customer-facing messages greet the **customer**, not the merchant).

## Multi-turn layer (`conversation_handlers.py`)

Handles the three replay-test scenarios (testing-brief §4 Phase 4) explicitly:
1. **Auto-reply detection** — tracks identical-text counts *per merchant*
   (not per conversation, since a fresh `conversation_id` doesn't reset a
   canned auto-reply pattern) plus a canned-phrase regex bank. Sends one
   gentle human-check on first sighting, ends by the 2nd/3rd repeat.
2. **Intent transition** — regex bank for explicit go-ahead ("let's do it",
   "chalo", "what's next") routes straight to action-mode language, skipping
   any further qualifying question (avoids the brief's Pattern-D failure).
3. **Hostile / opt-out** — ends immediately and politely on stop/spam/abuse
   signals; off-topic-but-polite asks get a brief redirect without
   abandoning the original thread.

## Tradeoffs

- **No LLM call** — trades ceiling-level creative phrasing for 100%
  determinism, zero latency risk against the 30s budget, zero API cost, and
  zero hallucination risk. Given the "don't fabricate" penalty is explicitly
  the harshest one in the rubric, this felt like the right trade for a
  60-minute live test window with a hard timeout.
- Only ~18 of 26 trigger kinds have bespoke copy; the rest share a
  well-parameterized fallback. Given more time I'd write bespoke handlers for
  `research_digest`'s non-dentist categories' `patient_content_library` /
  `trend_signals` fields, which are currently under-used.
- Hindi-English mixing is template-level connective phrases, not full
  sentence-level code-mixing — safer (no grammar risk) but less natural than
  a fluent bilingual speaker would produce.

## What additional context would have helped most

Real (non-placeholder) `payload` data for the generated triggers — the seed
triggers are rich, but ~80% of the 100 generated triggers only carry
`{"placeholder": true, "metric_or_topic": "..."}`, which meant building a
whole secondary "derive-a-real-fact-from-merchant-state" fallback path for
those. A `competitor_name` field on `competitor_opened` triggers would also
let that handler be genuinely specific instead of peer-benchmark-only.

## Deploying

```bash
pip install fastapi uvicorn
uvicorn bot:app --host 0.0.0.0 --port 8080
```

Deploy to any host with a public URL (Render/Fly/Railway/a VM + ngrok all
work — see `challenge-testing-brief.md` §6). No API keys required; the bot
has no external dependencies at all beyond FastAPI/uvicorn, so cold-start and
the 30s-per-call budget are non-issues.

Before submitting, edit the placeholder fields at the top of
`metadata()` in `bot.py` (`team_name`, `team_members`, `contact_email`).
