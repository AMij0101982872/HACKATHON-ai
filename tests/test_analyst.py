"""Analyste : politique fail-open et normalisation des verdicts."""
from src.analyst import Analyst, AnalystView


def test_fail_open_without_api_key():
    a = Analyst(api_key="")
    out = a.assess(["SPY", "AAPL"])
    assert set(out) == {"SPY", "AAPL"}
    assert all(v.ok for v in out.values())
    assert all(v.sentiment == "unknown" for v in out.values())
    assert "FEATHERLESS" in out["SPY"].note


def test_fail_open_when_news_fetch_raises(monkeypatch):
    a = Analyst(api_key="sk-fake")
    monkeypatch.setattr(a, "_headlines", lambda symbols: (_ for _ in ()).throw(RuntimeError("boom")))
    out = a.assess(["SPY"])
    assert out["SPY"].ok is True
    assert "indisponible" in out["SPY"].note or "news" in out["SPY"].note


def test_fail_open_when_llm_raises(monkeypatch):
    a = Analyst(api_key="sk-fake")
    monkeypatch.setattr(a, "_headlines", lambda symbols: {s: [] for s in symbols})
    monkeypatch.setattr(a, "_ask_llm", lambda h: (_ for _ in ()).throw(RuntimeError("no net")))
    out = a.assess(["SPY", "QQQ"])
    assert all(v.ok for v in out.values())


def test_verdict_mapping(monkeypatch):
    a = Analyst(api_key="sk-fake")
    monkeypatch.setattr(a, "_headlines", lambda symbols: {s: [] for s in symbols})
    monkeypatch.setattr(a, "_ask_llm", lambda h: {
        "SPY": {"verdict": "favorable", "why": "calme"},
        "AAPL": {"verdict": "unfavorable", "why": "résultats imminents"},
        "NVDA": {"verdict": "bizarre", "why": "?"},  # valeur inconnue -> neutral
    })
    out = a.assess(["SPY", "AAPL", "NVDA", "MSFT"])
    assert out["SPY"].ok is True and out["SPY"].sentiment == "favorable"
    assert out["AAPL"].ok is False and out["AAPL"].sentiment == "unfavorable"
    assert out["NVDA"].ok is True and out["NVDA"].sentiment == "neutral"
    assert out["MSFT"].ok is True  # absent de la réponse -> défaut permissif
