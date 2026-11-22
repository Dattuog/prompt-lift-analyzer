"""Generate a synthetic weekly CPG promo dataset shaped like dunnhumby's
"Breakfast at the Frat" (weekly units by product x store, with TPR, display
and feature flags). Effects are planted on purpose so the engine can be
checked against a known truth.

Usage: python -m engine.sample  -> data/sample_promo_data.csv
"""
from __future__ import annotations

import numpy as np
import pandas as pd

RNG = np.random.default_rng(42)
WEEKS = 104
STORES = 25

PRODUCTS = [
    # product_id, name, category, base_price, mean weekly units/store, cannibalization share
    ("CER-01", "Honey Oat Crunch 12oz", "Cold Cereal", 4.29, 38, 0.22),
    ("CER-02", "Choco Puffs 10oz", "Cold Cereal", 3.99, 30, 0.22),
    ("CER-03", "Fruit Rings 11oz", "Cold Cereal", 4.49, 22, 0.22),
    ("SNK-01", "Mini Pretzels 16oz", "Bag Snacks", 2.99, 45, 0.38),
    ("SNK-02", "Cheddar Crackers 8oz", "Bag Snacks", 3.49, 28, 0.38),
    ("SNK-03", "Veggie Straws 7oz", "Bag Snacks", 3.79, 16, 0.38),
    ("PIZ-01", "Pepperoni Pizza 12in", "Frozen Pizza", 6.49, 20, 0.12),
    ("PIZ-02", "Four Cheese Pizza 12in", "Frozen Pizza", 6.29, 15, 0.12),
    ("PIZ-03", "Supreme Pizza 12in", "Frozen Pizza", 6.99, 11, 0.12),
    ("ORL-01", "Mint Mouthwash 500ml", "Oral Hygiene", 5.49, 9, 0.08),
    ("ORL-02", "Whitening Mouthwash 500ml", "Oral Hygiene", 6.19, 6, 0.08),
]

MECHANICS = {
    "TPR only": (0, 0, 1.00),
    "TPR + Display": (1, 0, 1.45),
    "TPR + Feature": (0, 1, 1.35),
    "TPR + Display + Feature": (1, 1, 1.95),
}
ELASTICITY = 3.4
DIP = (0.18, 0.06)  # share of gross lift lost in the 1st and 2nd week after a promo


def build() -> pd.DataFrame:
    global RNG
    RNG = np.random.default_rng(42)  # reseed so every call returns the same data
    store_size = RNG.lognormal(0, 0.35, STORES)
    weeks = np.arange(1, WEEKS + 1)
    season = 1 + 0.12 * np.sin(2 * np.pi * weeks / 52)

    # expected (no-promo) demand
    base = {}
    for pid, _, cat, _, mu, _ in PRODUCTS:
        cat_season = season if cat != "Frozen Pizza" else 1 + 0.18 * np.cos(2 * np.pi * weeks / 52)
        base[pid] = np.outer(store_size * mu, cat_season)  # stores x weeks

    expected = {pid: b.copy() for pid, b in base.items()}
    promo = {pid: np.zeros((STORES, WEEKS), dtype=int) for pid in base}
    disp = {pid: np.zeros((STORES, WEEKS), dtype=int) for pid in base}
    feat = {pid: np.zeros((STORES, WEEKS), dtype=int) for pid in base}
    price = {pid: np.full((STORES, WEEKS), bp) for pid, _, _, bp, _, _ in PRODUCTS}

    for pid, _, cat, bp, _, cann in PRODUCTS:
        siblings = [p[0] for p in PRODUCTS if p[2] == cat and p[0] != pid]
        n_events = RNG.integers(6, 10)
        starts = np.sort(RNG.choice(np.arange(5, WEEKS - 4, 6), n_events, replace=False))
        for s in starts:
            length = RNG.choice([1, 2], p=[0.6, 0.4])
            mech = RNG.choice(list(MECHANICS))
            d_flag, f_flag, mult = MECHANICS[mech]
            depth = RNG.choice([0.10, 0.15, 0.20, 0.25, 0.30, 0.35])
            stores = RNG.choice(STORES, int(STORES * 0.6), replace=False)
            wk = np.arange(s, s + length)
            for st in stores:
                if promo[pid][st, wk].any() or promo[pid][st, s - 2:s].any():
                    continue
                gross_mult = (1 - depth) ** (-ELASTICITY) * mult
                gross = base[pid][st, wk] * (gross_mult - 1)
                expected[pid][st, wk] += gross
                promo[pid][st, wk] = 1
                disp[pid][st, wk] = d_flag
                feat[pid][st, wk] = f_flag
                price[pid][st, wk] = round(bp * (1 - depth), 2)
                # cannibalization: siblings lose a share of the lift, split by their size
                sib_w = np.array([base[sb][st, wk].sum() for sb in siblings])
                for sb, w in zip(siblings, sib_w / sib_w.sum()):
                    expected[sb][st, wk] -= np.minimum(gross * cann * w, base[sb][st, wk] * 0.6)
                # pantry-loading dip after the promo
                for k, share in enumerate(DIP, start=1):
                    t = s + length - 1 + k
                    if t < WEEKS:
                        expected[pid][st, t] -= min(gross.sum() * share, base[pid][st, t] * 0.6)

    rows = []
    for pid, name, cat, bp, _, _ in PRODUCTS:
        units = RNG.poisson(np.clip(expected[pid], 0.1, None))
        for st in range(STORES):
            for w in range(WEEKS):
                rows.append((w + 1, f"S{st + 1:03d}", pid, name, cat, int(units[st, w]),
                             float(price[pid][st, w]), bp, int(promo[pid][st, w]),
                             int(disp[pid][st, w]), int(feat[pid][st, w])))
    df = pd.DataFrame(rows, columns=["week", "store_id", "product_id", "product_name", "category",
                                     "units", "price", "base_price", "on_promo", "display", "feature"])
    df["revenue"] = (df["units"] * df["price"]).round(2)
    return df


if __name__ == "__main__":
    df = build()
    df.to_csv("data/sample_promo_data.csv", index=False)
    print(df.shape, df["on_promo"].mean().round(3))
