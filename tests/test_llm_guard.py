"""The LLM may only rephrase: invented facts are rejected, clean rewrites kept."""
import dataclasses

import vera.composer as composer
import vera.config as config
import vera.llm as llm


def _enable(monkeypatch, polished):
    cfg = dataclasses.replace(config.CONFIG, llm_provider="anthropic", llm_api_key="test", mode="challenge")
    monkeypatch.setattr(composer, "CONFIG", cfg)
    monkeypatch.setattr(llm, "polish", lambda payload: polished(payload))


def _inputs(data):
    m = data["merchants"]["m_001_drmeera_dentist_delhi"]
    return data["categories"]["dentists"], m, data["triggers"]["trg_001_research_digest_dentists"]


def test_hallucinated_rewrite_rejected(monkeypatch, data):
    cat, m, t = _inputs(data)
    base = composer.compose_full(cat, m, t, use_llm=False).body
    _enable(monkeypatch, lambda p: "Dr. Meera, 97% of 5,000 dentists in Delhi already switched — call me at 9am?")
    out = composer.compose_full(cat, m, t)
    assert out.body == base and not out.audit["llm_used"]


def test_clean_rewrite_accepted(monkeypatch, data):
    cat, m, t = _inputs(data)
    base = composer.compose_full(cat, m, t, use_llm=False).body
    rewrite = base.replace("just landed", "is out")        # wording change only; every fact kept
    _enable(monkeypatch, lambda p: rewrite)
    out = composer.compose_full(cat, m, t)
    assert out.body == rewrite and out.audit["llm_used"]


def test_llm_exception_falls_back(monkeypatch, data):
    cat, m, t = _inputs(data)
    base = composer.compose_full(cat, m, t, use_llm=False).body

    def boom(p):
        raise TimeoutError()
    cfg = dataclasses.replace(config.CONFIG, llm_provider="anthropic", llm_api_key="test")
    monkeypatch.setattr(composer, "CONFIG", cfg)
    monkeypatch.setattr(llm, "CONFIG", cfg)
    monkeypatch.setattr(llm, "_anthropic", boom)
    llm._cache.clear()
    out = composer.compose_full(cat, m, t)
    assert out.body == base
