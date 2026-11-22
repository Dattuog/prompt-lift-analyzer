import pandas as pd
import pytest

from engine.analysis import Settings, ValidationError, analyse


def make_fixture(promo_units=200, sib_units=30, dip=(80, 90)):
    """Product A (baseline 100/wk) promoted in week 15 at 20% off.
    Sibling B (baseline 50/wk) drops to `sib_units`. A dips after the promo."""
    rows = []
    for w in range(1, 31):
        a = 100
        if w == 15:
            a = promo_units
        elif w == 16:
            a = dip[0]
        elif w == 17:
            a = dip[1]
        rows.append(dict(week=w, store_id="S1", product_id="A", product_name="Prod A", category="Cat",
                         units=a, price=4.0 if w == 15 else 5.0, base_price=5.0,
                         on_promo=int(w == 15), display=int(w == 15), feature=0))
        rows.append(dict(week=w, store_id="S1", product_id="B", product_name="Prod B", category="Cat",
                         units=sib_units if w == 15 else 50, price=5.0, base_price=5.0,
                         on_promo=0, display=0, feature=0))
    return pd.DataFrame(rows)


def test_known_answer():
    r = analyse(make_fixture(), Settings(margin_pct=0.4))
    e = r["events"][0]
    assert e["baseline"] == 100
    assert e["gross_lift"] == 100
    assert e["cannibalized"] == 20
    assert e["dip"] == 30
    assert e["net_incremental"] == 50
    assert e["promo_cost"] == pytest.approx(200.0)          # $1 off x 200 units
    assert e["profit"] == pytest.approx(0.4 * 5 * 50 - 200)  # $100 margin - $200 cost
    assert e["roi"] == pytest.approx(-0.5)
    assert e["verdict"] == "lost money"
    assert e["mechanic"] == "Price cut + display"


def test_profitable_promo_is_marked_worked():
    r = analyse(make_fixture(promo_units=400, sib_units=50, dip=(100, 100)), Settings(margin_pct=0.4))
    e = r["events"][0]
    assert e["net_incremental"] == 300
    assert e["roi"] == pytest.approx((0.4 * 5 * 300 - 400) / 400)
    assert e["verdict"] == "worked"


def test_kpis_add_up():
    r = analyse(make_fixture())
    k = r["kpis"]
    assert k["events"] == 1
    assert k["leakage_pct"] == pytest.approx(0.5)


def test_missing_column_is_reported():
    with pytest.raises(ValidationError) as ex:
        analyse(make_fixture().drop(columns=["base_price"]))
    assert "base_price" in str(ex.value)


def test_bad_numbers_name_the_rows():
    df = make_fixture().astype({"units": object})
    df.loc[3, "units"] = "abc"
    with pytest.raises(ValidationError) as ex:
        analyse(df)
    assert "units" in str(ex.value) and "5" in str(ex.value)


def test_no_promos():
    df = make_fixture()
    df["on_promo"] = 0
    with pytest.raises(ValidationError):
        analyse(df)
