"""Plain-English readout.

The LLM never does math: it receives pre-formatted facts computed by
engine.analysis and must cite only those numbers. A guard rejects any
number in its output that is not in the facts; on failure (or with no API
key) a deterministic rule-based readout is returned instead.

Output schema (both LLM and rules):
  headline: str
  summary: str
  recommendations: [{action: "Do more"|"Stop"|"Change"|"Focus",
                     title: str, why: str,
                     metrics: [{label: str, value: str}], events: [str]}]
"""
from __future__ import annotations

import json
import os
import re

import httpx

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"
NUM_RE = re.compile(r"-?\d[\d,]*\.?\d*")
ACTIONS = ("Do more", "Stop", "Change", "Focus")


def pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:+.0f}%" if round(x * 100) != 0 else "0%"


def pct_plain(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.0f}%"


def build_facts(result: dict) -> dict:
    k = result["kpis"]
    ev = result["events"]

    def event_fact(e):
        return dict(event_id=e["event_id"], product=e["product_name"], category=e["category"],
                    promo_type=e["mechanic"], discount=pct_plain(e["depth"]), roi=pct(e["roi"]),
                    verdict=e["verdict"])

    def grp(rows, key):
        return [dict(name=r[key], events=r["events"], roi=pct(r["roi"]), paid_back=pct_plain(r["paid_back"]),
                     share_of_extra_sales_lost=pct_plain(r["leakage_pct"])) for r in rows]

    return dict(
        overview=dict(promotions=k["events"], products=k["products"], stores=k["stores"], weeks=k["weeks"],
                      paid_back=pct_plain(k["paid_back_pct"]), overall_roi=pct(k["overall_roi"]),
                      share_of_extra_sales_lost=pct_plain(k["leakage_pct"]),
                      assumed_brand_margin=pct_plain(k["margin_pct"])),
        by_promo_type=grp(result["by_mechanic"], "mechanic"),
        by_category=grp(result["by_category"], "category"),
        by_discount=grp(result["by_depth"], "depth_band"),
        best_promotions=[event_fact(e) for e in ev[:3]],
        worst_promotions=[event_fact(e) for e in ev[-3:][::-1]],
    )


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "").rstrip(".").lstrip("+") for n in NUM_RE.findall(text)}


def check_numbers(output: dict, facts: dict) -> list[str]:
    allowed = _numbers(json.dumps(facts)) | {str(i) for i in range(0, 11)}
    allowed |= {n.lstrip("-") for n in allowed}
    found = _numbers(json.dumps(output))
    return sorted(n for n in found if n.lstrip("-") not in allowed)


def _clean(out: dict) -> dict:
    """Keep only the schema fields, in the shape the UI expects."""
    recs = []
    for r in (out.get("recommendations") or [])[:4]:
        action = r.get("action") if r.get("action") in ACTIONS else "Change"
        metrics = [dict(label=str(m.get("label", ""))[:40], value=str(m.get("value", ""))[:16])
                   for m in (r.get("metrics") or [])[:3] if isinstance(m, dict)]
        events = [str(e) for e in (r.get("events") or [])[:4]]
        recs.append(dict(action=action, title=str(r.get("title", ""))[:90], why=str(r.get("why", ""))[:260],
                         metrics=metrics, events=events))
    return dict(headline=str(out.get("headline", ""))[:140], summary=str(out.get("summary", ""))[:400],
                recommendations=recs)


SYSTEM = (
    "You are writing for a busy brand manager at a consumer-goods company who is not a data analyst. "
    "You receive FACTS about past promotions computed by an analysis engine.\n"
    "Rules:\n"
    "- Use ONLY numbers that appear in FACTS, copied exactly. Never calculate or invent numbers.\n"
    "- Plain, everyday English. Short sentences. No jargon or abbreviations (no TPR, no ROI: say 'return').\n"
    "- 'share_of_extra_sales_lost' means extra sales that were really borrowed from sister products or from "
    "the weeks after the promotion. Describe it that way, not as 'leakage'.\n"
    "- Refer to promotions by product name; put their event_id only in the events list.\n"
    "Return ONLY JSON with this shape:\n"
    '{"headline": "one sentence, max 14 words",\n'
    ' "summary": "2 short sentences, max 45 words total",\n'
    ' "recommendations": [ {"action": "Do more" | "Stop" | "Change" | "Focus",\n'
    '    "title": "max 7 words", "why": "one sentence, max 25 words",\n'
    '    "metrics": [{"label": "2-4 plain words, e.g. Return or Paid back", "value": "number from FACTS"}] (1-2 items),\n'
    '    "events": ["E001"] (0-3 ids) } ] (exactly 3 or 4 items)}'
)


def llm_readout(result: dict, api_key: str | None = None, model: str | None = None, timeout: float = 40) -> dict:
    api_key = api_key or os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        return {**rule_based(result), "source": "rules", "note": "AI summary is not configured; showing the standard summary."}
    facts = build_facts(result)
    model = model or os.getenv("OPENROUTER_MODEL", DEFAULT_MODEL)
    msgs = [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": "FACTS:\n" + json.dumps(facts, indent=1)}]
    for _ in range(2):
        try:
            resp = httpx.post(OPENROUTER_URL, timeout=timeout,
                              headers={"Authorization": f"Bearer {api_key}", "X-Title": "Promo Lift Analyzer"},
                              json={"model": model, "messages": msgs, "temperature": 0.2})
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"]
            out = _clean(json.loads(text[text.find("{"): text.rfind("}") + 1]))
            bad = check_numbers(out, facts)
            if not bad and out["recommendations"]:
                return {**out, "source": "llm"}
            msgs.append({"role": "assistant", "content": text})
            msgs.append({"role": "user", "content": f"These numbers are not in FACTS: {bad}. Rewrite using only FACTS numbers. JSON only."})
        except Exception:  # network, quota, bad JSON
            pass
    return {**rule_based(result), "source": "rules", "note": "The AI summary is busy right now; showing the standard summary."}


def rule_based(result: dict) -> dict:
    f = build_facts(result)
    o = f["overview"]
    types, cat, disc = f["by_promo_type"], f["by_category"], f["by_discount"]
    best_t, worst_t = types[0], types[-1]
    best_d = disc[0]
    worst_c = max(cat, key=lambda c: float(c["share_of_extra_sales_lost"].rstrip("%"))
                  if c["share_of_extra_sales_lost"] != "n/a" else -1)
    best_e = f["best_promotions"][0]
    return dict(
        headline=f"Only {o['paid_back']} of {o['promotions']} promotions paid for themselves.",
        summary=(f"Overall, promotions returned {o['overall_roi']} on their discount cost. "
                 f"{o['share_of_extra_sales_lost']} of the extra sales were borrowed from sister products "
                 f"or from the weeks after the promotion."),
        recommendations=[
            dict(action="Do more", title=f"Use {best_t['name'].lower()}",
                 why=f"The best-performing promotion type: {best_t['paid_back']} of these promotions paid back.",
                 metrics=[dict(label="Return", value=best_t["roi"]), dict(label="Paid back", value=best_t["paid_back"])],
                 events=[]),
            dict(action="Stop", title=f"Avoid {worst_t['name'].lower()}",
                 why=f"{worst_t['name']} had the weakest return of any promotion type.",
                 metrics=[dict(label="Return", value=worst_t["roi"]), dict(label="Paid back", value=worst_t["paid_back"])],
                 events=[]),
            dict(action="Change", title=f"Keep discounts {best_d['name'].lower()}",
                 why=f"Smaller discounts lost the least money; {best_d['paid_back']} of them paid back.",
                 metrics=[dict(label="Return", value=best_d["roi"])], events=[]),
            dict(action="Focus", title=f"Promote one hero item in {worst_c['name']}",
                 why=f"In {worst_c['name']}, {worst_c['share_of_extra_sales_lost']} of the extra sales were taken "
                     f"from sister products or later weeks.",
                 metrics=[dict(label="Extra sales lost", value=worst_c["share_of_extra_sales_lost"])],
                 events=[]),
        ][:4] if best_e else [],
    )
