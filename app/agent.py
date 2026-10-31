"""SkinSync agent: an LLM with tools that turns YouCam scores into a routine, a basket and a try-on look.

Loop: model reads the analysis -> calls catalog tools (search_products, with optional foundation shade matching)
-> calls submit_plan with a structured plan. Every product id is validated against the catalog, and a
deterministic rule engine produces a plan if the LLM is unavailable or misbehaves.
"""
from __future__ import annotations

import json
import time
from typing import Any

from openai import AsyncOpenAI, BadRequestError

from . import catalog
from .catalog import CONCERN_LABELS, LOOKS
from .config import settings

SYSTEM = """You are SkinSync, a friendly skincare + beauty shopping agent inside a web shop.
You receive YouCam AI Skin Analysis scores (1-100, HIGHER = HEALTHIER; anything under ~70 is worth
attention) plus the shopper's detected skin colour and preferences.
Your job:
1. Explain the 3-4 most important findings in plain, kind, non-judgemental language (no medical claims,
   no diagnosis; suggest a dermatologist for persistent or painful acne/redness).
2. Build a simple AM and PM routine (3-5 steps each). AM must end with sunscreen. PM must include one
   targeted treatment (e.g. azelaic acid, BHA, retinal, peptides) for the lowest-scoring concerns. Avoid stacking strong
   actives in one routine (no retinoid + acid the same night, introduce retinoids 2-3 nights/week).
3. Pick real products ONLY by calling the tools; use only product ids returned by tools. Respect budget
   and fragrance-free / sensitive preferences. Reuse a product in AM and PM when sensible to save money.
4. Choose a makeup look and a foundation shade (pass skin_hex to search_products to get matches),
   a lipstick and a blush from the catalog that suit the person; this will be rendered with YouCam
   virtual try-on.
Looks available for try-on: fresh-dewy (everyday skin-first glow, light coverage), soft-glam (evening,
fuller coverage, defined lips), office-matte (shine control for oily T-zones).
Be efficient: make ONE search_products call with all the queries you need (include lipstick and blush
queries and skin_hex), then call submit_plan exactly once. Every 'why' is one short sentence."""

_CATEGORIES = ["cleanser", "toner", "serum", "treatment", "eye", "moisturizer", "sunscreen", "mask",
               "foundation", "lipstick", "blush"]
# Enums are described rather than enforced: strict providers (e.g. Groq) reject a whole tool call on one bad
# value, so we validate server-side and tell the model instead.
_QUERY = {"type": "object", "properties": {
    "category": {"type": "string", "description": "one of: " + ", ".join(_CATEGORIES)},
    "concern": {"type": "string", "description": "optional, one of: " + ", ".join(CONCERN_LABELS)},
    "max_price": {"type": "number"}}, "required": ["category"]}

TOOLS = [
    {"type": "function", "function": {
        "name": "search_products",
        "description": "Search the shop catalog with several queries at once (one per category/concern you need). "
                       "Returns up to 4 products per query: id, name, price (USD), concerns, actives, shade.",
        "parameters": {"type": "object", "properties": {
            "queries": {"type": "array", "items": _QUERY, "minItems": 1, "maxItems": 12},
            "skin_type": {"type": "string", "description": "optional: oily, combination, normal, dry or sensitive"},
            "fragrance_free": {"type": "boolean"},
            "skin_hex": {"type": "string", "description": "YouCam skin colour; returns the closest foundation shades"}},
            "required": ["queries"]}}},
    {"type": "function", "function": {
        "name": "submit_plan", "description": "Submit the final plan to show the shopper.",
        "parameters": {"type": "object", "properties": {
            "headline": {"type": "string", "description": "<= 10 words, upbeat"},
            "summary": {"type": "string", "description": "2-3 plain-language sentences"},
            "skin_type": {"type": "string"},
            "concerns": {"type": "array", "minItems": 3, "maxItems": 4, "items": {"type": "object", "properties": {
                "key": {"type": "string"}, "explanation": {"type": "string"}}, "required": ["key", "explanation"]}},
            "am_routine": {"type": "array", "items": {"type": "object", "properties": {
                "step": {"type": "string"}, "product_id": {"type": "string"}, "why": {"type": "string"}},
                "required": ["step", "product_id", "why"]}},
            "pm_routine": {"type": "array", "items": {"type": "object", "properties": {
                "step": {"type": "string"}, "product_id": {"type": "string"}, "why": {"type": "string"}},
                "required": ["step", "product_id", "why"]}},
            "weekly_tips": {"type": "array", "items": {"type": "string"}},
            "look": {"type": "object", "properties": {
                "look_id": {"type": "string", "description": "one of: " + ", ".join(LOOKS)},
                "foundation_id": {"type": "string"}, "lip_id": {"type": "string"}, "blush_id": {"type": "string"},
                "why": {"type": "string"}}, "required": ["look_id", "foundation_id", "lip_id", "blush_id", "why"]}},
            "required": ["headline", "summary", "skin_type", "concerns", "am_routine", "pm_routine", "look"]}}},
]


def _scores_text(analysis: dict) -> str:
    rows = sorted(analysis["concerns"].items(), key=lambda kv: kv[1]["ui_score"])
    return "\n".join(f"- {k} ({CONCERN_LABELS.get(k, k)}): {v['ui_score']}" for k, v in rows)


def _run_tool(name: str, args: dict) -> Any:
    if name == "search_products":
        out = {}
        for q in (args.get("queries") or [])[:12]:
            q = q if isinstance(q, dict) else {}
            if q.get("category") not in _CATEGORIES:
                out[str(q.get("category"))] = {"error": f"unknown category; use one of {_CATEGORIES}"}
                continue
            if q.get("concern") not in CONCERN_LABELS:
                q.pop("concern", None)
            key = q.get("category", "?") + (f":{q['concern']}" if q.get("concern") else "")
            st = args.get("skin_type") if args.get("skin_type") in {"oily", "combination", "normal", "dry", "sensitive"} else None
            res = catalog.search_products(category=q.get("category"), concern=q.get("concern"),
                                          max_price=q.get("max_price"), fragrance_free=args.get("fragrance_free"),
                                          skin_type=st, limit=3)
            if not res and q.get("concern"):  # relax the concern filter rather than return nothing
                res = catalog.search_products(category=q.get("category"), max_price=q.get("max_price"), limit=3)
            out[key] = [{"id": p["id"], "name": p["name"], "price": p["price"], "for": p["concerns"][:4],
                         "actives": p.get("key_ingredients", [])[:1], **({"shade": p["shade_hex"]} if "shade_hex" in p else {})}
                        for p in res]
        if args.get("skin_hex"):
            out["foundation_match"] = [{"id": p["id"], "name": p["name"], "price": p["price"], "shade": p["shade_hex"]}
                                       for p in catalog.match_foundation(args["skin_hex"])]
        return out
    if name == "match_foundation_shade":
        return catalog.match_foundation(args.get("skin_hex"))
    return {"error": f"unknown tool {name}"}


async def plan(analysis: dict, tone: dict, prefs: dict, previous: dict | None = None,
               instruction: str | None = None) -> dict:
    trace: list[dict] = []
    if settings.llm_enabled:
        try:
            result = await _llm_plan(analysis, tone, prefs, trace, previous, instruction)
            if result:
                return finalize(result, analysis, trace, engine=f"llm:{settings.llm_model}")
        except Exception as e:  # fall through to rules
            trace.append({"tool": "llm_error", "args": {}, "result": str(e)[:200]})
    return finalize(rule_plan(analysis, tone, prefs, instruction), analysis, trace, engine="rules")


async def _llm_plan(analysis, tone, prefs, trace, previous, instruction) -> dict | None:
    client = AsyncOpenAI(api_key=settings.llm_api_key or "injected-by-host", base_url=settings.llm_base_url,
                         timeout=45, max_retries=3)
    user = (f"YouCam skin analysis (overall {analysis.get('overall')}, skin age {analysis.get('skin_age')}):\n"
            f"{_scores_text(analysis)}\n\nYouCam skin colour: {tone.get('skin_color')}\n"
            f"Preferences: {json.dumps(prefs)}"
            + (f"\nSkincare routine budget: ${prefs['budget']} total for all unique AM+PM products (makeup excluded)."
               if prefs.get("budget") else ""))
    if previous and instruction:
        user += (f"\n\nYou previously proposed this plan:\n{json.dumps(_plan_brief(previous))}\n"
                 f"The shopper now says: \"{instruction}\". Revise the plan accordingly (search again if needed).")
    messages: list[dict] = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
    force = retried = False
    for turn in range(5):
        force = force or turn >= 3
        kwargs = dict(model=settings.llm_model, messages=messages, tools=TOOLS, temperature=0.4, max_tokens=1500,
                      tool_choice={"type": "function", "function": {"name": "submit_plan"}} if force else "auto",
                      extra_body={"reasoning_effort": "low"} if "gpt-oss" in settings.llm_model else None)
        try:
            resp = await client.chat.completions.create(**kwargs)
        except BadRequestError as e:  # malformed tool call from the model: one retry
            trace.append({"tool": "retry", "args": {}, "result": str(e)[:120]})
            resp = await client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message
        calls = msg.tool_calls or []
        if not calls:
            parsed = _json_from_text(msg.content or "")
            if parsed and parsed.get("am_routine"):
                trace.append({"tool": "submit_plan", "args": {}, "result": "plan returned as text"})
                return parsed
            messages.append({"role": "assistant", "content": (msg.content or "")[:1500]})
            messages.append({"role": "user", "content": "Call submit_plan now with the final plan."})
            force = True
            continue
        messages.append({"role": "assistant", "content": msg.content or "", "tool_calls": [
            {"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments}}
            for c in calls]})
        for c in calls:
            try:
                args = json.loads(c.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            if c.function.name == "submit_plan":
                total, budget = _routine_total(args), prefs.get("budget")
                if budget and total > budget * 1.05 and not retried:
                    # self-check: send the plan back once if it breaks the shopper's budget
                    retried, force = True, True
                    trace.append({"tool": "budget_check", "args": {"total": f"${total:g}", "budget": f"${budget:g}"},
                                  "result": "over budget, asked agent to revise"})
                    messages.append({"role": "tool", "tool_call_id": c.id, "content": json.dumps({
                        "rejected": f"Routine total ${total:g} exceeds the ${budget:g} budget. Drop optional steps "
                                    "or reuse products in AM and PM, then call submit_plan again."})})
                    continue
                trace.append({"tool": "submit_plan", "args": {"routine_total": f"${total:g}"}, "result": "plan accepted"})
                return args
            t0 = time.time()
            out = _run_tool(c.function.name, args)
            n = sum(len(v) for v in out.values() if isinstance(v, list)) if isinstance(out, dict) else len(out) if isinstance(out, list) else 0
            shown = ({"queries": ", ".join(k for k in out)} if c.function.name == "search_products" and isinstance(out, dict)
                     else args)
            trace.append({"tool": c.function.name, "args": shown, "result": f"{n} results",
                          "ms": int((time.time() - t0) * 1000)})
            messages.append({"role": "tool", "tool_call_id": c.id, "content": json.dumps(out)})
    return None


def _routine_total(plan: dict) -> float:
    ids = {str(st.get("product_id")) for st in (plan.get("am_routine") or []) + (plan.get("pm_routine") or [])
           if isinstance(st, dict)}
    return round(sum(p["price"] for p in (catalog.get_product(i) for i in ids) if p), 2)


def _json_from_text(text: str) -> dict | None:
    i, j = text.find("{"), text.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        return json.loads(text[i:j + 1])
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------- rule engine fallback
def rule_plan(analysis: dict, tone: dict, prefs: dict, instruction: str | None = None) -> dict:
    import re
    c = analysis["concerns"]
    ins = (instruction or "").lower()
    no_retinoid = bool(re.search(r"pregnan|retino|breastfeed|nursing", ins))
    bold = bool(re.search(r"bold|party|glam|evening|night out", ins))
    m = re.search(r"(?:under|below|max|less than)\s*\$?\s*(\d+)", ins)
    if m:
        prefs = dict(prefs, budget=float(m.group(1)))
    elif "cheap" in ins:
        prefs = dict(prefs, budget=min(prefs.get("budget") or 90, 60))
    worst = sorted(c, key=lambda k: c[k]["ui_score"])[:4]
    oily = c.get("oiliness", {}).get("ui_score", 80) < 65
    dry = c.get("moisture", {}).get("ui_score", 80) < 60 and not oily
    sensitive = bool(prefs.get("sensitive")) or c.get("redness", {}).get("ui_score", 80) < 60
    skin_type = "oily / combination" if oily else "dry" if dry else "normal"
    budget = prefs.get("budget")

    def pick(category, concerns, fallback=None):
        for con in concerns:
            r = catalog.search_products(category=category, concern=con, max_price=budget)
            if r:
                return r[0]["id"]
        r = catalog.search_products(category=category)
        return r[0]["id"] if r else fallback

    am = [
        {"step": "Cleanse", "product_id": "cl-01", "why": "Gentle, low-pH wash that won't aggravate redness."},
        {"step": "Treat", "product_id": pick("serum", [w for w in worst if w in ("pore", "oiliness", "acne")] +
                                                ["radiance"]), "why": "Targets your lowest-scoring concern."},
        {"step": "Moisturise", "product_id": "mo-01" if oily else "mo-02", "why": "Hydration matched to your oil level."},
        {"step": "Protect", "product_id": "sp-02" if sensitive else "sp-01", "why": "Daily SPF prevents spots and redness flare-ups."},
    ]
    pm = [
        {"step": "Cleanse", "product_id": "cl-02" if oily and not sensitive and not no_retinoid else "cl-01", "why": "Clears oil and sunscreen."},
        {"step": "Treat", "product_id": "se-04" if sensitive or no_retinoid else "tr-01",
         "why": "Azelaic acid calms redness and fades marks." if sensitive or no_retinoid else "Smooths texture and pores over time."},
        {"step": "Moisturise", "product_id": "mo-03" if sensitive else "mo-02", "why": "Repairs the skin barrier overnight."},
    ]
    if c.get("dark_circle_v2", {}).get("ui_score", 90) < 70:
        pm.insert(2, {"step": "Eye care", "product_id": "ey-01", "why": "Caffeine helps with dark circles and puffiness."})
    def total():
        ids = {s["product_id"] for s in am + pm}
        return sum(catalog.get_product(i)["price"] for i in ids if catalog.get_product(i))

    if budget:
        if total() > budget:  # 1) drop optional eye step
            pm[:] = [s for s in pm if s["step"] != "Eye care"]
        if total() > budget:  # 2) reuse the AM serum and moisturiser at night
            pm[1] = dict(am[1], why="Re-use your morning serum at night to keep costs down.")
            pm[-1] = dict(am[2], why="Same moisturiser morning and night keeps it simple.")
    fnd = catalog.match_foundation(tone.get("skin_color"), 1)[0]["id"]
    look = "soft-glam" if bold else "office-matte" if oily else "fresh-dewy"
    return {
        "headline": "Calm, clear and glowing — here's your plan",
        "summary": ("Your skin is in good overall shape. The biggest wins are calming "
                    f"{CONCERN_LABELS.get(worst[0], worst[0]).lower()} and {CONCERN_LABELS.get(worst[1], worst[1]).lower()} "
                    "with a gentle, consistent routine and daily sunscreen."),
        "skin_type": skin_type,
        "concerns": [{"key": k, "explanation": f"Scored {c[k]['ui_score']}/100 — one of your areas to focus on."} for k in worst],
        "am_routine": am, "pm_routine": pm,
        "weekly_tips": ["Clay mask once a week on the T-zone." if oily else "Hydrating sleeping mask twice a week.",
                        "Change pillowcases twice a week to reduce breakouts."],
        "look": {"look_id": look, "foundation_id": fnd, "lip_id": "lp-03" if bold else "lp-01", "blush_id": "bl-01",
                 "why": ("A shine-controlling, shade-matched base for an oily T-zone." if oily else
                         "A sheer, shade-matched base evens tone while letting skin show through.")},
    }


# ---------------------------------------------------------------- validation / enrichment
def _plan_brief(p: dict) -> dict:
    return {"am": [s["product"]["id"] for s in p.get("am_routine", [])],
            "pm": [s["product"]["id"] for s in p.get("pm_routine", [])],
            "look": {"look_id": p.get("look", {}).get("look_id"), "lip": p.get("look", {}).get("lip", {}).get("id"),
                     "blush": p.get("look", {}).get("blush", {}).get("id")}}


def _plan_core(p: dict) -> dict:
    return {k: p.get(k) for k in ("headline", "summary", "skin_type", "concerns", "am_routine", "pm_routine",
                                   "weekly_tips", "look")}


def finalize(raw: dict, analysis: dict, trace: list, engine: str) -> dict:
    def clean_routine(steps):
        out = []
        for s in steps or []:
            p = catalog.get_product(str(s.get("product_id", "")))
            if p:
                out.append({"step": s.get("step", p["category"].title()), "why": s.get("why", ""),
                            "product": catalog.compact(p)})
        return out

    am, pm = clean_routine(raw.get("am_routine")), clean_routine(raw.get("pm_routine"))
    look_raw = raw.get("look") or {}
    look_id = look_raw.get("look_id") if look_raw.get("look_id") in LOOKS else "fresh-dewy"

    def pick(pid, cat, default):
        p = catalog.get_product(str(pid or ""))
        if not p or p["category"] != cat:
            p = catalog.get_product(default)
        return catalog.compact(p)

    look = {"look_id": look_id, "name": LOOKS[look_id]["name"], "vibe": LOOKS[look_id]["vibe"],
            "why": look_raw.get("why", ""),
            "foundation": pick(look_raw.get("foundation_id"), "foundation", "fd-04"),
            "lip": pick(look_raw.get("lip_id"), "lipstick", "lp-01"),
            "blush": pick(look_raw.get("blush_id"), "blush", "bl-01")}
    concerns = []
    for c in raw.get("concerns") or []:
        k = c.get("key", "").replace("hd_", "")
        if k in analysis["concerns"]:
            concerns.append({"key": k, "label": CONCERN_LABELS.get(k, k), "score": analysis["concerns"][k]["ui_score"],
                             "explanation": c.get("explanation", "")})
    seen, basket = set(), []
    for s in am + pm:
        if s["product"]["id"] not in seen:
            seen.add(s["product"]["id"])
            basket.append(s["product"])
    return {"headline": raw.get("headline", "Your SkinSync plan"), "summary": raw.get("summary", ""),
            "skin_type": raw.get("skin_type", ""), "concerns": concerns, "am_routine": am, "pm_routine": pm,
            "weekly_tips": raw.get("weekly_tips") or [], "look": look, "basket": basket,
            "basket_total": round(sum(p["price"] for p in basket), 2), "engine": engine, "agent_trace": trace}
