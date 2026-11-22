"""Promo lift analysis engine.

Pure pandas, deterministic, no LLM. Input is weekly units per product x store
with promo flags; output is one row per promotion event with baseline, gross
lift, cannibalization, post-promo dip, net incremental units and brand ROI.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

REQUIRED = ["week", "store_id", "product_id", "product_name", "category",
            "units", "price", "base_price", "on_promo"]
OPTIONAL_FLAGS = ["display", "feature"]
BASELINE_WINDOW = 13   # weeks, centred
DIP_WEEKS = 2          # weeks after a promo checked for pantry loading
MAX_ROWS = 200_000


@dataclass
class Settings:
    margin_pct: float = 0.40     # brand gross margin as a share of base price
    worked_threshold: float = 0.05


class ValidationError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


# ---------------------------------------------------------------- validation
def validate(df: pd.DataFrame) -> pd.DataFrame:
    errors: list[str] = []
    df = df.rename(columns=lambda c: str(c).strip().lower())
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise ValidationError([f"Missing required column(s): {', '.join(missing)}"])
    if len(df) > MAX_ROWS:
        raise ValidationError([f"File has {len(df):,} rows; the demo limit is {MAX_ROWS:,}."])
    for c in OPTIONAL_FLAGS:
        if c not in df.columns:
            df[c] = 0
    for c in ["week", "units", "price", "base_price", "on_promo", "display", "feature"]:
        num = pd.to_numeric(df[c], errors="coerce")
        bad = num.isna() & df[c].notna() | df[c].isna()
        if bad.any():
            rows = (np.flatnonzero(bad)[:5] + 2).tolist()  # +2: header + 1-based
            errors.append(f"Column '{c}' has non-numeric or empty values (e.g. rows {rows})")
        df[c] = num
    if errors:
        raise ValidationError(errors)
    if (df["units"] < 0).any():
        errors.append("Column 'units' has negative values")
    if (df["base_price"] <= 0).any():
        errors.append("Column 'base_price' must be positive")
    if errors:
        raise ValidationError(errors)
    for c in ["store_id", "product_id", "product_name", "category"]:
        df[c] = df[c].astype(str)
    df[["week", "on_promo", "display", "feature"]] = df[["week", "on_promo", "display", "feature"]].astype(int)
    dup = df.duplicated(["week", "store_id", "product_id"])
    if dup.any():
        raise ValidationError([f"{int(dup.sum())} duplicate week/store/product rows"])
    return df.sort_values(["product_id", "store_id", "week"]).reset_index(drop=True)


# ------------------------------------------------------------------ baseline
def add_baseline(df: pd.DataFrame) -> pd.DataFrame:
    """Baseline = rolling median of 'clean' weeks (no promo, not in a post-promo
    dip window) for the same product-store, centred on the week."""
    df = df.copy()
    g = df.groupby(["product_id", "store_id"], sort=False)
    post = np.zeros(len(df), dtype=bool)
    for k in range(1, DIP_WEEKS + 1):
        post |= g["on_promo"].shift(k).fillna(0).astype(bool).to_numpy()
    df["post_promo"] = post & (df["on_promo"] == 0)
    clean = df["units"].where((df["on_promo"] == 0) & ~df["post_promo"])
    df["_clean"] = clean
    roll = (df.groupby(["product_id", "store_id"], sort=False)["_clean"]
              .transform(lambda s: s.rolling(BASELINE_WINDOW, center=True, min_periods=3).median()))
    fallback = df.groupby(["product_id", "store_id"], sort=False)["_clean"].transform("median")
    df["baseline"] = roll.fillna(fallback).fillna(df["units"])
    return df.drop(columns="_clean")


# -------------------------------------------------------------------- events
def _mechanic(display: int, feature: int) -> str:
    return {(0, 0): "Price cut only", (1, 0): "Price cut + display",
            (0, 1): "Price cut + ad", (1, 1): "Price cut + display + ad"}[(int(display), int(feature))]


def find_events(df: pd.DataFrame) -> pd.DataFrame:
    """One row per product-store promo run (consecutive promo weeks)."""
    p = df[df["on_promo"] == 1].copy()
    if p.empty:
        return pd.DataFrame()
    p["run"] = (p.groupby(["product_id", "store_id"])["week"].diff() != 1).cumsum()
    runs = p.groupby("run").agg(product_id=("product_id", "first"), store_id=("store_id", "first"),
                                start=("week", "min"), end=("week", "max"),
                                display=("display", "max"), feature=("feature", "max"))
    return runs.reset_index(drop=True)


def analyse(df: pd.DataFrame, settings: Settings | None = None) -> dict:
    s = settings or Settings()
    df = add_baseline(validate(df))
    df["gap"] = df["baseline"] - df["units"]          # positive = sold less than normal
    idx = df.set_index(["product_id", "store_id", "week"])
    info = df.drop_duplicates("product_id").set_index("product_id")[["product_name", "category", "base_price"]]
    siblings = {pid: info.index[(info["category"] == info.at[pid, "category"]) & (info.index != pid)].tolist()
                for pid in info.index}

    runs = find_events(df)
    if runs.empty:
        raise ValidationError(["No promoted weeks found (on_promo is never 1)"])

    recs = []
    for r in runs.itertuples(index=False):
        wk = list(range(r.start, r.end + 1))
        cur = idx.loc[(r.product_id, r.store_id)]
        ev = cur.loc[cur.index.intersection(wk)]
        post_wk = [w for w in range(r.end + 1, r.end + 1 + DIP_WEEKS)
                   if w in cur.index and cur.at[w, "on_promo"] == 0]
        dip = cur.loc[post_wk, "gap"].sum() if post_wk else 0.0
        cann = 0.0
        for sb in siblings[r.product_id]:
            if (sb, r.store_id) not in idx.index.droplevel(2):
                continue
            sc = idx.loc[(sb, r.store_id)]
            sw = [w for w in wk if w in sc.index and sc.at[w, "on_promo"] == 0]
            cann += sc.loc[sw, "gap"].sum() if sw else 0.0
        recs.append(dict(product_id=r.product_id, store_id=r.store_id, start=r.start, weeks=len(wk),
                         mechanic=_mechanic(r.display, r.feature),
                         depth=float((1 - ev["price"] / ev["base_price"]).mean()),
                         actual=float(ev["units"].sum()), baseline=float(ev["baseline"].sum()),
                         cannibalized=float(cann), dip=float(dip),
                         discount_cost=float(((ev["base_price"] - ev["price"]) * ev["units"]).sum())))
    st = pd.DataFrame(recs)

    # roll store-level runs up to chain-level events (same product, same start week)
    ev = (st.groupby(["product_id", "start", "mechanic"], as_index=False)
            .agg(weeks=("weeks", "max"), stores=("store_id", "nunique"), depth=("depth", "mean"),
                 actual=("actual", "sum"), baseline=("baseline", "sum"),
                 cannibalized=("cannibalized", "sum"), dip=("dip", "sum"),
                 discount_cost=("discount_cost", "sum")))
    ev = ev.join(info, on="product_id")
    ev["gross_lift"] = ev["actual"] - ev["baseline"]
    ev["net_incremental"] = ev["gross_lift"] - ev["cannibalized"] - ev["dip"]
    unit_margin = s.margin_pct * ev["base_price"]
    ev["incremental_margin"] = ev["net_incremental"] * unit_margin
    ev["promo_cost"] = ev["discount_cost"]
    ev["profit"] = ev["incremental_margin"] - ev["promo_cost"]
    ev["roi"] = np.where(ev["promo_cost"] > 0, ev["profit"] / ev["promo_cost"], 0.0)
    ev["lift_pct"] = np.where(ev["baseline"] > 0, ev["gross_lift"] / ev["baseline"], 0.0)
    ev["verdict"] = np.select([ev["roi"] > s.worked_threshold, ev["roi"] >= -s.worked_threshold],
                              ["worked", "broke even"], "lost money")
    ev = ev.sort_values("roi", ascending=False).reset_index(drop=True)
    ev.insert(0, "event_id", [f"E{i + 1:03d}" for i in range(len(ev))])
    return _summarise(ev, df, s)


# ----------------------------------------------------------------- summaries
def _group(ev: pd.DataFrame, key: str) -> list[dict]:
    g = ev.groupby(key).agg(events=("event_id", "count"),
                            roi=("profit", "sum"), cost=("promo_cost", "sum"),
                            gross_lift=("gross_lift", "sum"), net_incremental=("net_incremental", "sum"),
                            cannibalized=("cannibalized", "sum"), dip=("dip", "sum"),
                            paid_back=("verdict", lambda v: float((v == "worked").mean())))
    g["roi"] = g["roi"] / g["cost"].where(g["cost"] > 0)
    g["leakage_pct"] = (g["cannibalized"] + g["dip"]) / g["gross_lift"].where(g["gross_lift"] > 0)
    return _records(g.reset_index().sort_values("roi", ascending=False))


def _records(d: pd.DataFrame) -> list[dict]:
    out = d.replace([np.inf, -np.inf], np.nan).round(4)
    return [{k: (None if (isinstance(v, float) and np.isnan(v)) else v) for k, v in row.items()}
            for row in out.to_dict("records")]


def _summarise(ev: pd.DataFrame, df: pd.DataFrame, s: Settings) -> dict:
    ev = ev.copy()
    ev["depth_band"] = pd.cut(ev["depth"], [0, 0.15, 0.25, 1], labels=["Under 15%", "15-25%", "Over 25%"],
                              include_lowest=True).astype(str)
    cost = ev["promo_cost"].sum()
    kpis = dict(events=int(len(ev)), products=int(df["product_id"].nunique()),
                stores=int(df["store_id"].nunique()), weeks=int(df["week"].nunique()),
                paid_back_pct=round(float((ev["verdict"] == "worked").mean()), 4),
                gross_lift_units=round(float(ev["gross_lift"].sum()), 1),
                net_incremental_units=round(float(ev["net_incremental"].sum()), 1),
                leakage_pct=round(float((ev["cannibalized"].sum() + ev["dip"].sum())
                                        / max(ev["gross_lift"].sum(), 1e-9)), 4),
                promo_cost=round(float(cost), 2), profit=round(float(ev["profit"].sum()), 2),
                overall_roi=round(float(ev["profit"].sum() / cost), 4) if cost > 0 else 0.0,
                margin_pct=s.margin_pct)
    cols = ["event_id", "product_id", "product_name", "category", "start", "weeks", "stores", "mechanic",
            "depth", "baseline", "actual", "gross_lift", "lift_pct", "cannibalized", "dip",
            "net_incremental", "promo_cost", "incremental_margin", "profit", "roi", "verdict"]
    return dict(kpis=kpis, events=_records(ev[cols]),
                by_mechanic=_group(ev, "mechanic"), by_category=_group(ev, "category"),
                by_depth=_group(ev, "depth_band"), by_product=_group(ev.assign(product=ev["product_name"]), "product"))
