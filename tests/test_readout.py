import json
import pandas as pd
from engine.analysis import analyse
from engine.readout import build_facts, check_numbers, rule_based, llm_readout
from tests.test_analysis import make_fixture


def test_guard_flags_invented_numbers():
    facts = build_facts(analyse(make_fixture()))
    assert check_numbers({"summary": "ROI was -50%"}, facts) == []
    assert check_numbers({"summary": "ROI was 37%"}, facts) == ["37"]


def test_rule_based_only_uses_fact_numbers():
    r = analyse(make_fixture())
    assert check_numbers(rule_based(r), build_facts(r)) == []


def test_no_key_falls_back(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    out = llm_readout(analyse(make_fixture()))
    assert out["source"] == "rules" and out["recommendations"]
