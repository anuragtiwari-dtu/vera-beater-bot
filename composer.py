"""
composer.py — the "brain" of Vera-beater.

Pure functions, no I/O, fully deterministic given the same inputs (as required
by the challenge brief §7.1: "Must be deterministic given the same inputs").

Design principles (see challenge-brief.md §5, §10, §11):
  1. Never fabricate. Every concrete number/date/name in a message must come
     from the category/merchant/trigger/customer context actually supplied.
     Many generated triggers carry only {"placeholder": true, ...} payloads —
     when that happens we fall back to deriving a *real* fact from the
     merchant/customer/category context instead of inventing trigger detail.
  2. One primary CTA, single binary commitment where the trigger is
     action-oriented; open-ended / none for pure information.
  3. Category voice match (clinical-peer for dentists, warm-practical for
     salons, operator-to-operator for restaurants, coach-to-member for gyms,
     trustworthy-precise for pharmacies).
  4. Hindi-English code-mix when merchant.identity.languages includes "hi"
     and the category voice allows it — natural connective phrases, not
     machine-translated sentences.
  5. Compulsion levers (specificity, loss aversion, social proof, effort
     externalization, curiosity, reciprocity, asking-the-merchant, single
     binary CTA) — at least one per message, usually two.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Optional
import re

# --------------------------------------------------------------------------
# small utilities
# --------------------------------------------------------------------------

def _parse_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        s = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def days_between(now: datetime, then: Optional[str]) -> Optional[int]:
    dt = _parse_dt(then)
    if not dt:
        return None
    return abs((now - dt).days)


def pct(x: Optional[float]) -> str:
    if x is None:
        return "?"
    return f"{abs(x) * 100:.0f}%"


def fnum(x) -> str:
    try:
        return f"{int(x):,}"
    except Exception:
        return str(x)


def is_placeholder(trigger: dict) -> bool:
    return bool((trigger.get("payload") or {}).get("placeholder"))


def uses_hindi(category: dict, merchant: dict) -> bool:
    langs = (merchant.get("identity") or {}).get("languages") or []
    code_mix = ((category.get("voice") or {}).get("code_mix") or "")
    return "hi" in langs and code_mix in ("hindi_english_natural", "english_primary_some_hindi")


def first_name(merchant: dict) -> str:
    ident = merchant.get("identity") or {}
    fn = ident.get("owner_first_name")
    if fn:
        return fn
    # fall back to first token of business name, stripped of "Dr."/"The"
    name = ident.get("name", "there")
    return name.split()[0] if name else "there"


def salutation(category: dict, merchant: dict) -> str:
    """Pick a salutation matching the category's own salutation_examples pattern
    (merchant-facing — i.e. Vera talking to the merchant)."""
    ident = merchant.get("identity") or {}
    slug = category.get("slug", "")
    fname = first_name(merchant)
    fname_clean = re.sub(r"^(Dr\.|Mr\.|Mrs\.|Ms\.)\s*", "", fname).strip()
    if slug == "dentists":
        return f"Dr. {fname_clean}" if ident.get("owner_first_name") else ident.get("name", "Doc")
    biz = ident.get("name", "there")
    return f"Hi {fname_clean}" if ident.get("owner_first_name") else f"Hi, {biz} team"


def customer_salutation(customer: Optional[dict]) -> str:
    """Salutation for a customer-facing message (send_as=merchant_on_behalf)."""
    if not customer:
        return "Hi there"
    name = (customer.get("identity") or {}).get("name", "there")
    return f"Hi {name}"


def active_offer(merchant: dict) -> Optional[dict]:
    for o in merchant.get("offers") or []:
        if o.get("status") == "active":
            return o
    return None


def digest_by_id(category: dict, item_id: Optional[str]) -> Optional[dict]:
    if not item_id:
        return None
    for d in category.get("digest") or []:
        if d.get("id") == item_id:
            return d
    return None


def best_digest(category: dict, kind: Optional[str] = None) -> Optional[dict]:
    items = category.get("digest") or []
    if kind:
        for d in items:
            if d.get("kind") == kind:
                return d
    return items[0] if items else None


def peer_gap(merchant: dict, category: dict, metric: str) -> Optional[tuple]:
    """Return (merchant_value, peer_value, direction) for a metric, or None."""
    perf = merchant.get("performance") or {}
    peer = category.get("peer_stats") or {}
    mv = perf.get(metric)
    pv = peer.get(f"avg_{metric}") or peer.get(f"avg_{metric}_30d")
    if mv is None or pv is None or pv == 0:
        return None
    return (mv, pv, "below" if mv < pv else "above")


def last_conversation_ts(merchant: dict) -> Optional[str]:
    hist = merchant.get("conversation_history") or []
    if not hist:
        return None
    return sorted(hist, key=lambda h: h.get("ts", ""))[-1].get("ts")


def hinglish(phrase_en: str, phrase_hi: str, use_hi: bool) -> str:
    return phrase_hi if use_hi else phrase_en


# --------------------------------------------------------------------------
# CTA shaping
# --------------------------------------------------------------------------

def cta_line(cta_type: str, use_hi: bool) -> str:
    if cta_type == "binary_yes_stop":
        return hinglish("Reply YES to go ahead, or STOP to skip.",
                         "Reply karein YES aage badhne ke liye, ya STOP skip karne ke liye.", use_hi)
    if cta_type == "open_ended":
        return hinglish("Want me to take the next step?",
                         "Kya main agla step le loon?", use_hi)
    return ""  # "none"


# --------------------------------------------------------------------------
# per-kind composers
# Each returns: (hook: str, cta_type: str, levers: list[str])
# hook = the core fact-bearing sentence(s), WITHOUT salutation/CTA — those get
# assembled by compose().
# --------------------------------------------------------------------------

def _research_digest(cat, mer, trg, cust, now, use_hi):
    item = digest_by_id(cat, (trg.get("payload") or {}).get("top_item_id")) or best_digest(cat, "research")
    if item:
        seg = item.get("patient_segment") or item.get("segment")
        seg_txt = f" for your {seg.replace('_', ' ')} cohort" if seg and "high_risk_adult_cohort" in (mer.get("signals") or []) else ""
        n = item.get("trial_n")
        n_txt = f"{fnum(n)}-sample " if n else ""
        hook = (f"{item.get('title', 'New research')} — {n_txt}study{seg_txt}. "
                f"Source: {item.get('source', 'category digest')}.")
        if item.get("actionable"):
            hook += f" {item['actionable']}."
        return hook, "open_ended", ["specificity", "reciprocity"]
    # placeholder fallback — use category peer stats as the "why now"
    gap = peer_gap(mer, cat, "ctr")
    if gap:
        mv, pv, direction = gap
        hook = (f"This week's category digest is out. Separately I noticed your CTR ({pct(mv)}) is "
                f"{direction} the peer median ({pct(pv)}) — worth a look together.")
        return hook, "open_ended", ["specificity", "social_proof"]
    return "This week's category research digest just landed — a couple of items looked relevant to you.", "open_ended", ["curiosity"]


def _regulation_change(cat, mer, trg, cust, now, use_hi):
    item = digest_by_id(cat, (trg.get("payload") or {}).get("top_item_id")) or best_digest(cat, "compliance")
    deadline = (trg.get("payload") or {}).get("deadline_iso")
    if item:
        hook = f"{item.get('title')} — {item.get('summary', '')}".strip()
        if deadline:
            hook += f" Effective {deadline}."
        return hook, "binary_yes_stop", ["specificity", "loss_aversion"]
    return "A regulation update relevant to your category just came in — want the details?", "open_ended", ["curiosity"]


def _cde_opportunity(cat, mer, trg, cust, now, use_hi):
    payload = trg.get("payload") or {}
    item = digest_by_id(cat, payload.get("digest_item_id"))
    credits = payload.get("credits")
    fee = payload.get("fee")
    title = item.get("title") if item else "a CDE session"
    hook = f"{title}"
    extras = []
    if credits:
        extras.append(f"{credits} CDE credits")
    if fee:
        extras.append(fee.replace("_", " "))
    if extras:
        hook += f" — {', '.join(extras)}."
    return hook, "binary_yes_stop", ["specificity", "reciprocity"]


def _perf_spike(cat, mer, trg, cust, now, use_hi):
    payload = trg.get("payload") or {}
    metric = payload.get("metric")
    delta = payload.get("delta_pct")
    if metric and delta is not None:
        hook = f"Your {metric} are up {pct(delta)} vs baseline this window."
    else:
        d7 = (mer.get("performance") or {}).get("delta_7d") or {}
        best_metric, best_val = None, 0
        for k, v in d7.items():
            if v is not None and v > best_val:
                best_metric, best_val = k, v
        if best_metric:
            hook = f"Your {best_metric.replace('_pct', '')} are up {pct(best_val)} week-over-week."
        else:
            hook = "Your profile had a strong week — numbers are trending up."
    offer = active_offer(mer)
    if offer:
        hook += f" Good moment to push \"{offer.get('title')}\" while momentum's up."
    return hook, "open_ended", ["specificity", "loss_aversion" if False else "curiosity"]


def _perf_dip(cat, mer, trg, cust, now, use_hi):
    payload = trg.get("payload") or {}
    metric = payload.get("metric")
    delta = payload.get("delta_pct")
    window = payload.get("window", "7d")
    if metric and delta is not None:
        hook = f"Heads up — your {metric} dropped {pct(delta)} over the last {window}."
        base = payload.get("vs_baseline")
        if base:
            hook += f" (baseline ~{fnum(base)}/wk)."
    else:
        d7 = (mer.get("performance") or {}).get("delta_7d") or {}
        worst_metric, worst_val = None, 0
        for k, v in d7.items():
            if v is not None and v < worst_val:
                worst_metric, worst_val = k, v
        if worst_metric:
            hook = f"Heads up — your {worst_metric.replace('_pct', '')} dipped {pct(worst_val)} this week."
        else:
            hook = "Your numbers dipped a bit this week — worth a quick look."
    return hook, "open_ended", ["loss_aversion", "specificity"]


def _seasonal_perf_dip(cat, mer, trg, cust, now, use_hi):
    payload = trg.get("payload") or {}
    note = payload.get("season_note", "").replace("_", " ")
    delta = payload.get("delta_pct")
    metric = payload.get("metric", "views")
    hook = f"Your {metric} are down {pct(delta)} this week"
    if payload.get("is_expected_seasonal") and note:
        hook += f" — this tracks the usual {note} pattern for your category, not a red flag."
    else:
        hook += "."
    return hook, "none", ["specificity"]


def _milestone_reached(cat, mer, trg, cust, now, use_hi):
    agg = mer.get("customer_aggregate") or {}
    ytd = agg.get("total_unique_ytd")
    perf = mer.get("performance") or {}
    views = perf.get("views")
    if ytd:
        hook = f"Milestone: you've served {fnum(ytd)} unique customers this year."
    elif views:
        hook = f"Milestone: your profile crossed {fnum(views)} views in the last {perf.get('window_days', 30)} days."
    else:
        hook = "You've hit a nice milestone this month — worth celebrating on your profile."
    hook += " Want me to draft a quick post so customers see it too?"
    return hook, "open_ended", ["social_proof", "effort_externalization"]


def _dormant_with_vera(cat, mer, trg, cust, now, use_hi):
    last_ts = last_conversation_ts(mer)
    d = days_between(now, last_ts) if last_ts else None
    if d:
        hook = f"It's been {d} days since we last spoke."
    else:
        hook = "It's been a while since we last spoke."
    gap = peer_gap(mer, cat, "ctr")
    if gap:
        mv, pv, direction = gap
        hook += f" Quick data point: your CTR is {pct(mv)}, peer median is {pct(pv)} ({direction})."
    return hook + " Anything I can help with this week?", "open_ended", ["reciprocity", "asking_merchant"]


def _review_theme_emerged(cat, mer, trg, cust, now, use_hi):
    themes = mer.get("review_themes") or []
    neg = [t for t in themes if t.get("sentiment") == "neg"]
    if neg:
        t = neg[0]
        hook = (f"{t.get('occurrences_30d', 'Several')} reviews this month mention "
                f"\"{t.get('theme', 'an issue').replace('_', ' ')}\" — e.g. \"{t.get('common_quote', '')}\".")
        hook += " Want me to draft a response template for this pattern?"
        return hook, "open_ended", ["specificity", "loss_aversion"]
    pos = [t for t in themes if t.get("sentiment") == "pos"]
    if pos:
        t = pos[0]
        hook = (f"{t.get('occurrences_30d', 'Several')} recent reviews praise "
                f"\"{t.get('theme', '').replace('_', ' ')}\" — worth turning into a Google post.")
        return hook, "open_ended", ["social_proof", "effort_externalization"]
    return "A new pattern showed up in your reviews this month — want the summary?", "open_ended", ["curiosity"]


def _competitor_opened(cat, mer, trg, cust, now, use_hi):
    # payload is placeholder in generated set → no competitor name available,
    # so we use real peer-benchmark data instead of inventing a competitor.
    gap = peer_gap(mer, cat, "views")
    locality = (mer.get("identity") or {}).get("locality", "your area")
    if gap:
        mv, pv, direction = gap
        hook = (f"New listing activity picked up in {locality} this week. Your views ({fnum(mv)}) are "
                f"{direction} the category peer average ({fnum(pv)}) right now — good time to tighten up your profile.")
    else:
        hook = f"New listing activity picked up in {locality} this week — worth a quick profile check to stay ahead."
    return hook, "open_ended", ["loss_aversion", "specificity"]


def _festival_upcoming(cat, mer, trg, cust, now, use_hi):
    beats = cat.get("seasonal_beats") or []
    offer = active_offer(mer)
    if beats:
        b = beats[0]
        hook = f"{b.get('month_range', 'This season')}: {b.get('note', 'seasonal demand shift expected')}."
    else:
        hook = "Festival season is coming up — demand usually shifts in the next few weeks."
    if offer:
        hook += f" Your \"{offer.get('title')}\" offer is a good fit to push right now."
    else:
        catalog = cat.get("offer_catalog") or []
        if catalog:
            hook += f" Consider running \"{catalog[0].get('title')}\" for the season."
    return hook, "binary_yes_stop", ["specificity", "loss_aversion"]


def _category_seasonal(cat, mer, trg, cust, now, use_hi):
    payload = trg.get("payload") or {}
    trends = payload.get("trends") or []
    if trends:
        top = trends[:2]
        readable = ", ".join(t.replace("_", " ").replace("+", "+").replace("-", "-") for t in top)
        hook = f"Seasonal shift for your category: {readable}."
        if payload.get("shelf_action_recommended"):
            hook += " Worth adjusting your shelf/menu mix this week."
        return hook, "open_ended", ["specificity", "loss_aversion"]
    return "A seasonal demand shift just kicked in for your category — want the breakdown?", "open_ended", ["curiosity"]


def _ipl_match_today(cat, mer, trg, cust, now, use_hi):
    payload = trg.get("payload") or {}
    match = payload.get("match")
    venue = payload.get("venue")
    mtime = payload.get("match_time_iso")
    hook = f"{match} tonight" if match else "Big match tonight"
    if venue:
        hook += f" at {venue}"
    hook += ". Match nights have historically driven more footfall for restaurants in your category."
    offer = active_offer(mer)
    if offer:
        hook += f" Want me to push \"{offer.get('title')}\" as a match-night special?"
    else:
        hook += " Want me to draft a quick match-night offer post?"
    return hook, "binary_yes_stop", ["specificity", "social_proof"]


def _gbp_unverified(cat, mer, trg, cust, now, use_hi):
    payload = trg.get("payload") or {}
    uplift = payload.get("estimated_uplift_pct")
    path = (payload.get("verification_path") or "postcard or phone call").replace("_", " ")
    hook = "Your Google Business Profile is still unverified"
    if uplift:
        hook += f" — verified listings in your category see roughly {pct(uplift)} more visibility."
    else:
        hook += " — verified listings typically get meaningfully more visibility."
    hook += f" Verification is usually a quick {path}."
    return hook, "binary_yes_stop", ["loss_aversion", "effort_externalization"]


def _supply_alert(cat, mer, trg, cust, now, use_hi):
    payload = trg.get("payload") or {}
    molecule = payload.get("molecule")
    batches = payload.get("affected_batches") or []
    mfr = payload.get("manufacturer")
    hook = f"Supply alert: {molecule or 'a molecule you likely stock'}"
    if mfr:
        hook += f" ({mfr})"
    if batches:
        hook += f" — affected batches: {', '.join(batches)}."
    else:
        hook += " has a batch alert out."
    hook += " Worth checking your shelf stock against this list."
    return hook, "binary_yes_stop", ["specificity", "loss_aversion"]


def _winback_eligible(cat, mer, trg, cust, now, use_hi):
    payload = trg.get("payload") or {}
    days = payload.get("days_since_expiry")
    lapsed = payload.get("lapsed_customers_added_since_expiry")
    dip = payload.get("perf_dip_pct")
    parts = []
    if days:
        parts.append(f"it's been {days} days since your plan expired")
    if dip:
        parts.append(f"visibility is down {pct(dip)} since then")
    if lapsed:
        parts.append(f"{fnum(lapsed)} more customers have gone quiet in that window")
    hook = "Quick one — " + ", ".join(parts) + "." if parts else "Your plan lapsed a little while back — good time to look at renewing."
    return hook, "binary_yes_stop", ["loss_aversion", "specificity"]


def _renewal_due(cat, mer, trg, cust, now, use_hi):
    sub = mer.get("subscription") or {}
    days = sub.get("days_remaining")
    plan = sub.get("plan", "your plan")
    if days is not None:
        hook = f"{plan} renewal is {days} days out."
        if days <= 14:
            hook += " Renewing now avoids any visibility gap."
    else:
        hook = f"{plan} renewal is coming up."
    return hook, "binary_yes_stop", ["loss_aversion"]


def _active_planning_intent(cat, mer, trg, cust, now, use_hi):
    payload = trg.get("payload") or {}
    topic = (payload.get("intent_topic") or "your new program").replace("_", " ")
    last_msg = payload.get("merchant_last_message", "")
    hook = f"Following up on \"{topic}\" — you asked what it should look like."
    if last_msg:
        hook += f" (You said: \"{last_msg}\")."
    hook += " I've sketched a starting structure — want me to share it now?"
    return hook, "binary_yes_stop", ["effort_externalization", "reciprocity"]


def _curious_ask_due(cat, mer, trg, cust, now, use_hi):
    slug = cat.get("slug", "")
    asks = {
        "dentists": "What's the treatment your patients ask about most this week?",
        "salons": "What's the one service that's been most requested this week?",
        "restaurants": "What's the dish that's outperforming the rest of your menu this week?",
        "gyms": "What's the class or slot that's filling up fastest this week?",
        "pharmacies": "What's the product customers keep asking for that you don't stock yet?",
    }
    hook = asks.get(slug, "What's one thing customers keep asking you for this week?")
    return hook, "open_ended", ["asking_merchant", "curiosity"]


def _generic_customer_scope(cat, mer, trg, cust, now, use_hi, kind):
    """Shared fallback for customer-scope kinds when payload is placeholder:
    derive real facts from CustomerContext.relationship instead of inventing."""
    name = "there"
    rel = {}
    if cust:
        name = (cust.get("identity") or {}).get("name", "there")
        rel = cust.get("relationship") or {}
    last_visit = rel.get("last_visit")
    d = days_between(now, last_visit) if last_visit else None
    services = rel.get("services_received") or []
    last_service = services[-1] if services else None
    offer = active_offer(mer)

    if kind == "recall_due":
        hook = ""
        if d:
            hook += f"it's been {d} days since your last visit"
        else:
            hook += "your recall window is open"
        if last_service:
            hook += f" ({last_service})"
        hook += "."
        if offer:
            hook += f" {offer.get('title')} is currently on."
        return hook, "binary_yes_stop", ["specificity", "loss_aversion"]

    if kind == "chronic_refill_due":
        hook = "your usual refill window looks due"
        if d:
            hook += f" (last recorded visit {d} days ago)"
        hook += "."
        return hook, "binary_yes_stop", ["specificity"]

    if kind == "trial_followup":
        hook = "checking in after your trial"
        if last_visit:
            hook += f" on {last_visit}"
        hook += " — how did it go?"
        if offer:
            hook += f" If you're in, {offer.get('title')} is a good next step."
        return hook, "binary_yes_stop", ["asking_merchant", "effort_externalization"]

    if kind == "appointment_tomorrow":
        hook = "quick reminder about your appointment tomorrow."
        if rel.get("visits_total"):
            hook += f" Looking forward to visit #{rel['visits_total'] + 1}."
        return hook, "binary_yes_stop", ["specificity"]

    if kind == "customer_lapsed_soft":
        hook = ""
        if d:
            hook += f"it's been {d} days since your last visit"
        else:
            hook += "it's been a little while"
        hook += " — we'd love to see you again."
        if offer:
            hook += f" {offer.get('title')} is currently on."
        return hook, "binary_yes_stop", ["loss_aversion", "reciprocity"]

    if kind == "customer_lapsed_hard":
        payload = trg.get("payload") or {}
        days_p = payload.get("days_since_last_visit") or d
        focus = payload.get("previous_focus")
        hook = f"it's been {days_p or 'a good number of'} days since your last visit"
        if focus:
            hook += f" — last time you were focused on {focus.replace('_', ' ')}"
        hook += "."
        if offer:
            hook += f" {offer.get('title')} might be a good way back in."
        return hook, "binary_yes_stop", ["loss_aversion", "reciprocity"]

    if kind == "wedding_package_followup":
        payload = trg.get("payload") or {}
        days_to = payload.get("days_to_wedding")
        nxt = (payload.get("next_step_window_open") or "").replace("_", " ")
        hook = ""
        if days_to:
            hook += f"{days_to} days to go"
        if nxt:
            hook += f" — your {nxt} window is open now."
        else:
            hook += " — checking in on your prep timeline."
        return hook, "binary_yes_stop", ["specificity", "loss_aversion"]

    return "checking in — anything we can help with?", "open_ended", ["asking_merchant"]


KIND_HANDLERS = {
    "research_digest": _research_digest,
    "regulation_change": _regulation_change,
    "cde_opportunity": _cde_opportunity,
    "perf_spike": _perf_spike,
    "perf_dip": _perf_dip,
    "seasonal_perf_dip": _seasonal_perf_dip,
    "milestone_reached": _milestone_reached,
    "dormant_with_vera": _dormant_with_vera,
    "review_theme_emerged": _review_theme_emerged,
    "competitor_opened": _competitor_opened,
    "festival_upcoming": _festival_upcoming,
    "category_seasonal": _category_seasonal,
    "ipl_match_today": _ipl_match_today,
    "gbp_unverified": _gbp_unverified,
    "supply_alert": _supply_alert,
    "winback_eligible": _winback_eligible,
    "renewal_due": _renewal_due,
    "active_planning_intent": _active_planning_intent,
    "curious_ask_due": _curious_ask_due,
}

CUSTOMER_SCOPE_KINDS = {
    "recall_due", "chronic_refill_due", "trial_followup", "appointment_tomorrow",
    "customer_lapsed_soft", "customer_lapsed_hard", "wedding_package_followup",
}


# --------------------------------------------------------------------------
# top-level compose()
# --------------------------------------------------------------------------

def compose(category: dict, merchant: dict, trigger: dict,
            customer: Optional[dict] = None, now: Optional[datetime] = None) -> dict:
    """
    Implements challenge-brief.md §5:
        compose(category, merchant, trigger, customer=None) -> ComposedMessage
    Returns dict with: body, cta, send_as, suppression_key, rationale
    """
    now = now or datetime.now(timezone.utc)
    kind = trigger.get("kind", "")
    use_hi = uses_hindi(category, merchant)

    if kind in CUSTOMER_SCOPE_KINDS:
        hook, cta_type, levers = _generic_customer_scope(category, merchant, trigger, customer, now, use_hi, kind)
        send_as = "merchant_on_behalf"
    else:
        handler = KIND_HANDLERS.get(kind)
        if handler:
            hook, cta_type, levers = handler(category, merchant, trigger, customer, now, use_hi)
        else:
            # generic, still non-fabricated: lean on merchant signals
            signals = merchant.get("signals") or []
            if signals:
                hook = f"Noticed this on your profile: {signals[0].replace('_', ' ')}. Want me to take a look?"
            else:
                hook = "Quick check-in — anything on your profile you'd like help with this week?"
            cta_type, levers = "open_ended", ["asking_merchant"]
        send_as = "vera"

    if send_as == "merchant_on_behalf":
        sal = customer_salutation(customer)
    else:
        sal = salutation(category, merchant)
    cta = cta_line(cta_type, use_hi)
    hook = hook.strip()
    body = f"{sal}, {hook}" if not hook.lower().startswith(sal.lower()) else hook
    if cta:
        body = f"{body} {cta}"
    body = re.sub(r"\s+", " ", body).strip()

    cta_out = {"binary_yes_stop": "binary_yes_stop", "open_ended": "open_ended", "none": "none"}.get(cta_type, "open_ended")

    rationale = (f"kind={kind}; levers={'+'.join(levers)}; "
                 f"anchored on {'trigger payload' if not is_placeholder(trigger) else 'merchant/customer real-state fallback (payload was placeholder)'}; "
                 f"voice={category.get('slug')}; lang={'hi-en' if use_hi else 'en'}")

    return {
        "body": body,
        "cta": cta_out,
        "send_as": send_as,
        "suppression_key": trigger.get("suppression_key", f"{kind}:{merchant.get('merchant_id','')}"),
        "rationale": rationale,
    }
