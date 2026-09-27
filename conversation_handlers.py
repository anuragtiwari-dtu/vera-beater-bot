"""
conversation_handlers.py — multi-turn reply logic.

Implements challenge-brief.md §12 (open challenges) and the three replay-test
scenarios in challenge-testing-brief.md §4 Phase 4:
  1. Auto-reply hell — detect canned WhatsApp Business auto-replies and exit
     gracefully rather than burning turns (brief §3, pain point #1).
  2. Intent transition — when the merchant signals explicit "yes let's do it",
     switch from qualifying to action mode immediately (brief §3, pain point
     #2 and §9 Pattern D).
  3. Hostile / off-topic — stay polite, end on a clear opt-out/hostile signal,
     don't get derailed by unrelated questions.

This module is deliberately independent of any specific web framework so it
can be unit-tested in isolation; bot.py wires it to the HTTP layer.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Optional


# --------------------------------------------------------------------------
# pattern banks
# --------------------------------------------------------------------------

AUTO_REPLY_PATTERNS = [
    r"thank you for contacting", r"will respond shortly", r"will get back to you",
    r"currently unavailable", r"automated (message|assistant|reply)",
    r"we (have received|received) your message", r"business hours are",
    r"team tak pahuncha", r"team ko bata", r"jald hi (jawab|reply)",
]

INTENT_PATTERNS = [
    r"\blet'?s do it\b", r"\bgo ahead\b", r"\bi'?m in\b", r"\bsounds good\b",
    r"\bok(ay)?,?\s*(let'?s|lets|proceed|go ahead)", r"\byes,?\s*(please|do it|proceed)",
    r"\bchalo\b", r"\bhaan\b.*\bkar(o|do)\b", r"\bkarna hai\b", r"\bjoin\b.*\b(hai|karna)\b",
    r"\bi want to join\b", r"\bwhat'?s next\b", r"\bwhats next\b", r"\bproceed\b",
]

OPTOUT_HOSTILE_PATTERNS = [
    r"\bstop\b", r"\bunsubscribe\b", r"\bspam\b", r"\bleave me alone\b",
    r"\bnot interested\b", r"\bdon'?t (message|contact|text) me\b", r"\bstop messaging\b",
    r"\buseless\b", r"\bharass", r"\bnuisance\b", r"\bblock\b",
]

WAIT_PATTERNS = [
    r"\blater\b", r"\bbusy\b", r"\bgive me (some|a) time\b", r"\bnot now\b",
    r"\bcall you back\b", r"\bafter (some|a) time\b", r"\bthodi der\b", r"\bbaad mein\b",
]

QUALIFYING_WORDS = ("would you", "do you want", "can you tell", "what if", "how about", "are you interested")
ACTIONING_WORDS = ("done", "sending", "draft", "here", "confirm", "proceed", "next", "sharing", "sent", "locking")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _matches_any(text: str, patterns: list[str]) -> bool:
    t = _norm(text)
    return any(re.search(p, t) for p in patterns)


# --------------------------------------------------------------------------
# conversation state
# --------------------------------------------------------------------------

@dataclass
class ConversationState:
    conversation_id: str
    merchant_id: Optional[str] = None
    customer_id: Optional[str] = None
    turns: list = field(default_factory=list)          # [{"from": "...", "msg": "..."}]
    sent_bodies: set = field(default_factory=set)       # anti-repetition
    ended: bool = False
    merchant_msg_counts: dict = field(default_factory=dict)  # normalized text -> count, GLOBAL per merchant


def respond(state: ConversationState, merchant_message: str,
            merchant: Optional[dict] = None, category: Optional[dict] = None) -> dict:
    """
    Given the conversation so far + the merchant's latest message, produce the
    next move. Returns one of the three shapes from testing-brief.md §2.3:
        {"action": "send", "body": ..., "cta": ..., "rationale": ...}
        {"action": "wait", "wait_seconds": ..., "rationale": ...}
        {"action": "end", "rationale": ...}
    """
    norm = _norm(merchant_message)

    # 1) Hostile / explicit opt-out — always highest priority, end politely.
    if _matches_any(norm, OPTOUT_HOSTILE_PATTERNS):
        return {"action": "end",
                "rationale": "Merchant signalled opt-out/hostility; ending respectfully without further pitching."}

    # 2) Auto-reply detection. Track how many times we've seen this exact
    #    normalized text from this merchant, globally (not just this
    #    conversation) — a real merchant rarely sends the identical string
    #    twice, but WA Business canned auto-replies do.
    key = norm
    count = state.merchant_msg_counts.get(key, 0) + 1
    state.merchant_msg_counts[key] = count
    looks_canned = _matches_any(norm, AUTO_REPLY_PATTERNS)

    if count >= 3 or (looks_canned and count >= 2):
        return {"action": "end",
                "rationale": f"Same message seen {count}x (canned-pattern={looks_canned}); "
                              f"this is very likely the merchant's WhatsApp Business auto-reply, not a human. "
                              f"Exiting gracefully rather than burning further turns."}
    if looks_canned and count == 1:
        # First time seeing a canned-looking reply: try once, politely, to reach a human.
        return {"action": "send",
                "body": "Samajh gayi — if this is an auto-reply, no worries. If you're free for 2 minutes, "
                        "I can show you exactly what's missing on your profile. Otherwise I'll check back later.",
                "cta": "open_ended",
                "rationale": "Possible auto-reply on first sight; one gentle human-check before disengaging."}

    # 3) Explicit intent transition — switch to action mode immediately,
    #    do NOT ask another qualifying question (brief §9 Pattern D).
    if _matches_any(norm, INTENT_PATTERNS):
        body = ("Great — locking this in now. I'm drafting the next step and will share it in a moment. "
                "Anything specific you want prioritised first?")
        return {"action": "send", "body": body, "cta": "open_ended",
                "rationale": "Merchant gave explicit go-ahead; switched from qualifying to action mode immediately."}

    # 4) Merchant asked for time / said busy — back off, don't push.
    if _matches_any(norm, WAIT_PATTERNS):
        return {"action": "wait", "wait_seconds": 1800,
                "rationale": "Merchant asked for time; backing off 30 minutes rather than re-pitching."}

    # 5) Off-topic but not hostile — acknowledge briefly, stay on mission.
    off_topic_markers = ("gst", "tax", "loan", "legal", "insurance")
    if any(w in norm for w in off_topic_markers) and not any(w in norm for w in ("profile", "offer", "campaign", "review")):
        body = ("That's outside what I can help with directly, but happy to point you to magicpin support for it. "
                "On my side — want me to keep going with the profile update we were discussing?")
        return {"action": "send", "body": body, "cta": "open_ended",
                "rationale": "Off-topic ask; redirected politely without abandoning the original mission."}

    # 6) Default: acknowledge + advance with a concrete next step, avoiding
    #    exact repetition of anything already sent in this conversation.
    body = "Got it — noted. I'll take the next step and follow up shortly with what I find."
    if body in state.sent_bodies:
        body = "Noted, thanks for confirming. Give me a moment to pull that together."
    return {"action": "send", "body": body, "cta": "open_ended",
            "rationale": "Generic acknowledgement + forward motion; no strong signal (intent/hostile/wait) detected."}
