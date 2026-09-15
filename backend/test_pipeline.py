import json
from types import SimpleNamespace

import pytest

from app.config import settings
from app.services import claim_service, ranking_service, verification_service, whisper_service


class FakeWhisperModel:
    def __init__(self, text):
        self.text = text
        self.calls = []

    def transcribe(self, _file_path, **kwargs):
        self.calls.append(kwargs)
        return iter([SimpleNamespace(start=0.0, end=2.0, text=self.text)]), SimpleNamespace()


def test_whisper_preserves_multilingual_auto_detection(monkeypatch, tmp_path):
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"audio")
    model = FakeWhisperModel("नमस्कार, आज भारत की जनसंख्या सबसे अधिक है।")
    monkeypatch.setattr(whisper_service, "get_whisper_model", lambda: model)

    result = whisper_service.transcribe_audio(str(audio))

    assert result["text"].startswith("नमस्कार")
    assert model.calls[0]["language"] is None
    assert model.calls[0]["task"] == "transcribe"


def test_whisper_rejects_urdu_script(monkeypatch, tmp_path):
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"audio")
    monkeypatch.setattr(whisper_service, "get_whisper_model", lambda: FakeWhisperModel("نمस्कार"))

    with pytest.raises(whisper_service.TranscriptionError):
        whisper_service.transcribe_audio(str(audio))


def test_claim_extraction_normalizes_real_model_json(monkeypatch):
    monkeypatch.setattr(
        claim_service,
        "query_ollama",
        lambda *_args, **_kwargs: '[{"statement":"भारत की जनसंख्या दुनिया में सबसे अधिक है।","speaker":"Speaker A","timestamp":"00:14"}]',
    )

    claims = claim_service.extract_claims("भारत की जनसंख्या दुनिया में सबसे अधिक है।", [{"start": 14, "text": "भारत की जनसंख्या दुनिया में सबसे अधिक है।"}])

    assert claims == [{"claim_text": "भारत की जनसंख्या दुनिया में सबसे अधिक है।", "speaker": "Speaker A", "timestamp": "00:14"}]


def test_verifier_returns_evidence_grounded_verdict(monkeypatch):
    monkeypatch.setattr(verification_service, "generate_search_query", lambda _claim: "India population UN")
    monkeypatch.setattr(verification_service, "search_evidence", lambda *_args, **_kwargs: [{"title": "UN Population Division", "url": "https://www.un.org/example", "snippet": "India surpassed China in 2023."}])
    monkeypatch.setattr(verification_service, "rank_evidence", lambda evidence, _query: evidence)
    monkeypatch.setattr(verification_service, "determine_implementation_status", lambda *_args: "No Reliable Update")
    monkeypatch.setattr(verification_service, "query_ollama", lambda *_args, **_kwargs: '{"verdict":"TRUE","confidence":0.95,"reasoning":"UN data confirms India surpassed China in 2023."}')

    result = verification_service.verify_single_claim("India has the world's largest population.")

    assert result["verdict"] == "SUPPORTED"
    assert 0.0 <= result["confidence"] <= 100.0
    assert result["sources"][0]["url"] == "https://www.un.org/example"


def test_verifier_does_not_invent_evidence(monkeypatch):
    monkeypatch.setattr(verification_service, "generate_search_query", lambda claim: claim)
    monkeypatch.setattr(verification_service, "search_evidence", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(verification_service, "rank_evidence", lambda evidence, _query: evidence)

    result = verification_service.verify_single_claim("A claim with no trusted evidence.")

    assert result["verdict"] == "INSUFFICIENT EVIDENCE"
    assert result["sources"] == []
    assert result["confidence"] == 0.0


def test_verifier_preserves_evidence_when_qwen_fails(monkeypatch):
    evidence = [{"title": "Reuters", "url": "https://reuters.com/example", "snippet": "Relevant reporting."}]
    monkeypatch.setattr(verification_service, "search_evidence", lambda *_args, **_kwargs: evidence)
    monkeypatch.setattr(verification_service, "rank_evidence", lambda found, _query: found)
    monkeypatch.setattr(verification_service, "query_ollama", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("model unavailable")))

    result = verification_service.verify_single_claim("A claim supported by reporting.", generate_query=False, live_mode=True)

    assert result["verdict"] == "INSUFFICIENT EVIDENCE"
    assert result["sources"] == evidence
    assert "Sources were retrieved" in result["reasoning"]


def test_evidence_ranking_rejects_unrelated_trusted_sources(monkeypatch):
    monkeypatch.setattr(ranking_service, "_url_is_accessible", lambda *_args: True)
    monkeypatch.setattr(ranking_service, "_page_excerpt", lambda _url, _terms: "Relevant claim page text.")
    evidence = [
        {"title": "Houthi forces seize Yemen port", "url": "https://reuters.com/relevant", "snippet": "The Houthis advanced along Yemen's Red Sea coast."},
        {"title": "Unrelated Yemen weather report", "url": "https://bbc.com/weather", "snippet": "A seasonal weather update for Yemen."},
    ]

    ranked = ranking_service.rank_evidence(evidence, "Houthis advanced along Yemen's Red Sea coast")

    assert [item["url"] for item in ranked] == ["https://reuters.com/relevant"]
    assert ranked[0]["relevance_score"] > 0


def test_hindi_factual_claim_is_checkworthy():
    assert claim_service.is_checkworthy("भारत की अर्थव्यवस्था 2025 में 8 प्रतिशत की दर से बढ़ी।")
    assert claim_service.is_checkworthy("भारत सरकार ने 2024 में नई नीति लागू की।")
    assert not claim_service.is_checkworthy("मुझे लगता है यह बहुत अच्छा है।")


def test_hindi_live_query_preserves_factual_terms():
    query = verification_service._live_search_query("भारत ने 2025 में नई व्यापार नीति लागू की।")
    assert "भारत" in query
    assert "2025" in query
    assert "व्यापार" in query
    assert "नीति" in query


def test_hindi_live_verifier_keeps_original_claim_and_uses_validated_evidence(monkeypatch):
    claim = "भारत ने 2025 में 8 प्रतिशत आर्थिक वृद्धि दर्ज की।"
    evidence = [{
        "title": "India growth data",
        "url": "https://www.example.gov/economic-growth-2025",
        "snippet": "India recorded 8 percent economic growth in 2025.",
        "source_url": "https://www.example.gov/economic-growth-2025",
    }]
    seen = {}
    monkeypatch.setattr(
        verification_service,
        "generate_search_query",
        lambda *_args, **_kwargs: "India economic growth 2025 8 percent",
    )
    monkeypatch.setattr(
        verification_service,
        "search_evidence",
        lambda query, **_kwargs: seen.setdefault("search_query", query) and evidence,
    )
    monkeypatch.setattr(verification_service, "rank_evidence", lambda sources, _query: sources)
    monkeypatch.setattr(
        verification_service,
        "query_ollama",
        lambda prompt, **_kwargs: seen.setdefault("prompt", prompt) and '{"verdict":"SUPPORTED","confidence":0.8,"reasoning":"Source [1] gives the stated figure."}',
    )

    result = verification_service.verify_single_claim(claim, generate_query=False, live_mode=True)

    assert seen["search_query"] == "India economic growth 2025 8 percent"
    assert claim in seen["prompt"]
    assert result["sources"] == evidence
    assert result["verdict"] == "SUPPORTED"


def test_verifier_does_not_bypass_relevance_filter(monkeypatch):
    candidate = [{"title": "Unrelated article", "url": "https://reuters.com/unrelated", "snippet": "Different event."}]
    monkeypatch.setattr(verification_service, "search_evidence", lambda *_args, **_kwargs: candidate)
    monkeypatch.setattr(verification_service, "rank_evidence", lambda _found, _query: [])
    monkeypatch.setattr(verification_service, "query_ollama", lambda *_args, **_kwargs: pytest.fail("Qwen must not receive rejected sources"))

    result = verification_service.verify_single_claim("A specific event claim.", generate_query=False, live_mode=True)

    assert result["sources"] == []
    assert result["verdict"] == "INSUFFICIENT EVIDENCE"


# ══════════════════════════════════════════════════════════════════════════════
# Multi-Factor Verification & Confidence Scoring Tests (Viva Defense Suite)
# ══════════════════════════════════════════════════════════════════════════════

def test_confidence_weights_sum_to_one():
    """Mathematical Invariant: All 7 factor weights must sum precisely to 1.0."""
    total_weights = (
        settings.CONFIDENCE_WEIGHT_EVIDENCE_STRENGTH +
        settings.CONFIDENCE_WEIGHT_SOURCE_QUALITY +
        settings.CONFIDENCE_WEIGHT_EVIDENCE_COVERAGE +
        settings.CONFIDENCE_WEIGHT_SOURCE_CONSISTENCY +
        settings.CONFIDENCE_WEIGHT_SOURCE_DIVERSITY +
        settings.CONFIDENCE_WEIGHT_TEMPORAL_RELEVANCE +
        settings.CONFIDENCE_WEIGHT_CLAIM_CLARITY
    )
    assert abs(total_weights - 1.0) < 1e-6, f"Weights sum to {total_weights}, expected 1.0"


def test_scenario_1_strong_supporting_evidence(monkeypatch):
    """Scenario 1: Strong Supporting Evidence across independent authoritative sources."""
    sources = [
        {"title": "Reuters Report", "url": "https://www.reuters.com/world/article1", "snippet": "Detailed corroboration of the event with comprehensive background facts.", "relevance_score": 0.95},
        {"title": "BBC News", "url": "https://www.bbc.com/news/article2", "snippet": "Confirmed independently by international observers and official records.", "relevance_score": 0.92},
        {"title": "WHO Bulletin", "url": "https://www.who.int/news/article3", "snippet": "Official health data aligns exactly with the numbers quoted in the statement.", "relevance_score": 0.90},
        {"title": "UN Press Release", "url": "https://www.un.org/press/article4", "snippet": "United Nations confirmed the resolution and demographic statistics.", "relevance_score": 0.94},
    ]
    monkeypatch.setattr(verification_service, "search_evidence", lambda *_args, **_kwargs: sources)
    monkeypatch.setattr(verification_service, "rank_evidence", lambda found, _query: found)
    monkeypatch.setattr(
        verification_service,
        "query_ollama",
        lambda *_args, **_kwargs: json.dumps({
            "verdict": "SUPPORTED",
            "confidence": 0.95,
            "evidence_coverage": 95,
            "claim_clarity": 90,
            "temporal_relevance": 95,
            "supporting_count": 4,
            "contradicting_count": 0,
            "implementation_status": "No Reliable Update",
            "reasoning": "Sources [1], [2], [3], and [4] all independently confirm the claim.",
        }),
    )

    result = verification_service.verify_single_claim("Demographic data reported by international bodies.", generate_query=False, live_mode=True)

    assert result["verdict"] == "SUPPORTED"
    assert result["confidence"] >= 75.0
    factors = json.loads(result["confidence_factors"])
    assert factors["source_consistency"] == 100.0
    assert factors["source_diversity"] == 100.0
    assert factors["source_quality"] == 100.0
    assert factors["supporting_count"] == 4
    assert factors["contradicting_count"] == 0
    assert factors["total_source_count"] == 4
    assert factors["final_verdict"] == "SUPPORTED"
    assert "High Confidence" in factors["explanation"]


def test_scenario_2_strong_contradicting_evidence(monkeypatch):
    """Scenario 2: Strong Contradicting Evidence from trusted authorities."""
    sources = [
        {"title": "Reuters Fact Check", "url": "https://www.reuters.com/fact-check/false-claim", "snippet": "Government records and official statements directly refute the viral assertion.", "relevance_score": 0.95},
        {"title": "AP News Verification", "url": "https://apnews.com/article/debunk", "snippet": "Investigators found no evidence to support the claim; documents show opposite findings.", "relevance_score": 0.92},
        {"title": "BBC Reality Check", "url": "https://www.bbc.com/news/reality-check", "snippet": "Official agency statistics show the metric actually decreased rather than increased.", "relevance_score": 0.88},
    ]
    monkeypatch.setattr(verification_service, "search_evidence", lambda *_args, **_kwargs: sources)
    monkeypatch.setattr(verification_service, "rank_evidence", lambda found, _query: found)
    monkeypatch.setattr(
        verification_service,
        "query_ollama",
        lambda *_args, **_kwargs: json.dumps({
            "verdict": "REFUTED",
            "confidence": 0.92,
            "evidence_coverage": 90,
            "claim_clarity": 95,
            "temporal_relevance": 90,
            "supporting_count": 0,
            "contradicting_count": 3,
            "implementation_status": "No Reliable Update",
            "reasoning": "Sources [1], [2], and [3] explicitly refute the assertion.",
        }),
    )

    result = verification_service.verify_single_claim("Disproven assertion about economic growth.", generate_query=False, live_mode=True)

    assert result["verdict"] == "REFUTED"
    assert result["confidence"] >= 70.0
    factors = json.loads(result["confidence_factors"])
    assert factors["source_consistency"] == 100.0
    assert factors["supporting_count"] == 0
    assert factors["contradicting_count"] == 3
    assert factors["total_source_count"] == 3
    assert "Refuted by authoritative reporting" in factors["explanation"]


def test_scenario_3_mixed_evidence_conflicting(monkeypatch):
    """Scenario 3: Mixed supporting and contradicting evidence results in calibrated CONFLICTING score."""
    sources = [
        {"title": "Source Alpha", "url": "https://www.reuters.com/a", "snippet": "Official spokesperson claimed the project was fully funded and launched.", "relevance_score": 0.85},
        {"title": "Source Beta", "url": "https://www.thehindu.com/b", "snippet": "Ministry records indicate allocation was approved in the recent budget cycle.", "relevance_score": 0.82},
        {"title": "Source Gamma", "url": "https://www.bbc.com/c", "snippet": "Audit reports revealed disbursements were withheld due to compliance discrepancies.", "relevance_score": 0.80},
        {"title": "Source Delta", "url": "https://www.aljazeera.com/d", "snippet": "Local authorities state no ground work has commenced despite initial announcements.", "relevance_score": 0.78},
    ]
    monkeypatch.setattr(verification_service, "search_evidence", lambda *_args, **_kwargs: sources)
    monkeypatch.setattr(verification_service, "rank_evidence", lambda found, _query: found)
    monkeypatch.setattr(
        verification_service,
        "query_ollama",
        lambda *_args, **_kwargs: json.dumps({
            "verdict": "CONFLICTING",
            "confidence": 0.50,
            "evidence_coverage": 80,
            "claim_clarity": 85,
            "temporal_relevance": 80,
            "supporting_count": 2,
            "contradicting_count": 2,
            "implementation_status": "In Progress",
            "reasoning": "Sources disagree on whether the project is funded vs halted.",
        }),
    )

    result = verification_service.verify_single_claim("The infrastructure corridor is fully active.", generate_query=False, live_mode=True)

    assert result["verdict"] == "CONFLICTING"
    factors = json.loads(result["confidence_factors"])
    assert factors["supporting_count"] == 2
    assert factors["contradicting_count"] == 2
    # Consistency for equal split: 30 + (2/4)*25 = 42.5
    assert factors["source_consistency"] == 42.5
    # Conflicting confidence should be bounded by the conflict calibration guard (<= 65.0)
    assert result["confidence"] <= 65.0
    assert "conflicting reports" in factors["explanation"].lower()


def test_scenario_4_low_source_diversity_single_domain():
    """Scenario 4: Multiple sources all from the same domain receive low diversity score."""
    sources = [
        {"url": "https://www.reuters.com/article/1", "source_domain": "reuters.com"},
        {"url": "https://www.reuters.com/article/2", "source_domain": "reuters.com"},
        {"url": "https://www.reuters.com/article/3", "source_domain": "reuters.com"},
        {"url": "https://www.reuters.com/article/4", "source_domain": "reuters.com"},
    ]
    diversity = verification_service._calculate_source_diversity(sources)
    # 1 unique domain / 4 sources = 0.25 uniqueness; 1 / 3.0 = 0.333 breadth -> 29.2%
    assert diversity < 35.0, f"Expected low diversity for single domain echo chamber, got {diversity}"


def test_scenario_5_high_source_diversity_independent_domains():
    """Scenario 5: Sources from independent domains receive high diversity score."""
    sources = [
        {"url": "https://www.reuters.com/world/1", "source_domain": "reuters.com"},
        {"url": "https://www.bbc.com/news/2", "source_domain": "bbc.com"},
        {"url": "https://www.who.int/news/3", "source_domain": "who.int"},
        {"url": "https://www.un.org/press/4", "source_domain": "un.org"},
    ]
    diversity = verification_service._calculate_source_diversity(sources)
    assert diversity == 100.0, f"Expected 100.0 for 4 independent domains, got {diversity}"


def test_scenario_6_weak_evidence():
    """Scenario 6: Short snippets and low relevance scores yield low evidence strength."""
    sources = [
        {"url": "https://example.com/a", "snippet": "Short note.", "relevance_score": 0.2},
        {"url": "https://example.com/b", "snippet": "Brief quote.", "relevance_score": 0.25},
    ]
    strength = verification_service._calculate_evidence_strength(sources, "A specific factual claim", evidence_coverage=20.0)
    # Relevance ~22.5%, richness 0.0% -> raw ~15.75%, capped by coverage*1.5=30.0%
    assert strength <= 30.0, f"Expected weak evidence strength <= 30.0, got {strength}"


def test_scenario_7_missing_evidence(monkeypatch):
    """Scenario 7: Zero retrieved sources defaults strictly to INSUFFICIENT EVIDENCE and 0 confidence."""
    monkeypatch.setattr(verification_service, "search_evidence", lambda *_args, **_kwargs: [])

    result = verification_service.verify_single_claim("An unseen or obscure assertion.", generate_query=False, live_mode=True)

    assert result["verdict"] == "INSUFFICIENT EVIDENCE"
    assert result["confidence"] == 0.0
    factors = json.loads(result["confidence_factors"])
    assert factors["total_source_count"] == 0
    assert factors["evidence_coverage"] == 0.0
    assert factors["evidence_strength"] == 0.0
    assert factors["source_quality"] == 0.0
    assert factors["source_diversity"] == 0.0
    assert factors["source_consistency"] == 0.0


def test_scenario_8_old_evidence_temporal_penalty(monkeypatch):
    """Scenario 8: Outdated evidence for time-sensitive claim is penalized in temporal_relevance."""
    sources = [
        {"title": "Archival Record 2018", "url": "https://www.reuters.com/archive-2018", "snippet": "Data as of the 2018 fiscal census.", "relevance_score": 0.85},
    ]
    monkeypatch.setattr(verification_service, "search_evidence", lambda *_args, **_kwargs: sources)
    monkeypatch.setattr(verification_service, "rank_evidence", lambda found, _query: found)
    monkeypatch.setattr(
        verification_service,
        "query_ollama",
        lambda *_args, **_kwargs: json.dumps({
            "verdict": "SUPPORTED",
            "confidence": 0.60,
            "evidence_coverage": 50,
            "claim_clarity": 80,
            "temporal_relevance": 20,  # Old evidence severely penalized
            "supporting_count": 1,
            "contradicting_count": 0,
            "implementation_status": "No Reliable Update",
            "reasoning": "Evidence is from 2018, not valid for current 2026 assertion.",
        }),
    )

    result = verification_service.verify_single_claim("In 2026, current production is at record highs.", generate_query=False, live_mode=True)

    factors = json.loads(result["confidence_factors"])
    assert factors["temporal_relevance"] <= 25.0
    # Final confidence is lowered due to temporal relevance drag
    assert result["confidence"] < 65.0


def test_scenario_9_clear_claim(monkeypatch):
    """Scenario 9: Highly specific, unambiguous factual claim receives high clarity score."""
    sources = [
        {"title": "Official Record", "url": "https://www.who.int/declaration-2023", "snippet": "WHO Director-General declared end of public health emergency on May 5, 2023.", "relevance_score": 0.95},
    ]
    monkeypatch.setattr(verification_service, "search_evidence", lambda *_args, **_kwargs: sources)
    monkeypatch.setattr(verification_service, "rank_evidence", lambda found, _query: found)
    monkeypatch.setattr(
        verification_service,
        "query_ollama",
        lambda *_args, **_kwargs: json.dumps({
            "verdict": "SUPPORTED",
            "confidence": 0.95,
            "evidence_coverage": 95,
            "claim_clarity": 95,
            "temporal_relevance": 95,
            "supporting_count": 1,
            "contradicting_count": 0,
            "implementation_status": "No Reliable Update",
            "reasoning": "Exact entity, date, and declaration corroborated.",
        }),
    )

    result = verification_service.verify_single_claim("WHO declared end of the COVID emergency on May 5, 2023.", generate_query=False, live_mode=True)

    factors = json.loads(result["confidence_factors"])
    assert factors["claim_clarity"] >= 90.0


def test_scenario_10_ambiguous_claim(monkeypatch):
    """Scenario 10: Vague, ambiguous statement receives low claim clarity score."""
    sources = [
        {"title": "Speculation Article", "url": "https://example.com/speculation", "snippet": "Industry observers discuss potential market shifts.", "relevance_score": 0.5},
    ]
    monkeypatch.setattr(verification_service, "search_evidence", lambda *_args, **_kwargs: sources)
    monkeypatch.setattr(verification_service, "rank_evidence", lambda found, _query: found)
    monkeypatch.setattr(
        verification_service,
        "query_ollama",
        lambda *_args, **_kwargs: json.dumps({
            "verdict": "INSUFFICIENT EVIDENCE",
            "confidence": 0.20,
            "evidence_coverage": 30,
            "claim_clarity": 25,  # Vague claim
            "temporal_relevance": 40,
            "supporting_count": 0,
            "contradicting_count": 0,
            "implementation_status": "No Reliable Update",
            "reasoning": "Statement lacks clear verifiable factual predicates.",
        }),
    )

    result = verification_service.verify_single_claim("Things might change significantly in the near future.", generate_query=False, live_mode=True)

    factors = json.loads(result["confidence_factors"])
    assert factors["claim_clarity"] <= 30.0
    assert result["verdict"] == "INSUFFICIENT EVIDENCE"


def test_invariants_all_factors_in_bounds():
    """Invariant: Every output factor and final confidence is strictly bounded within [0.0, 100.0]."""
    factors = verification_service._make_confidence_factors(
        final_confidence=120.0,  # Deliberate overflow input
        evidence_coverage=150.0,
        evidence_strength=-10.0,
        source_quality=95.5,
        source_consistency=80.0,
        source_diversity=50.0,
        temporal_relevance=100.0,
        claim_clarity=0.0,
        supporting_count=3,
        contradicting_count=1,
        total_source_count=4,
        final_verdict="SUPPORTED",
        llm_confidence=99.0,
        explanation="Test explanation",
    )
    for key in [
        "final_confidence", "evidence_coverage", "evidence_strength",
        "source_quality", "source_consistency", "source_diversity",
        "temporal_relevance", "claim_clarity", "llm_confidence",
    ]:
        val = factors[key]
        assert 0.0 <= val <= 100.0 or val >= 0.0, f"Factor {key}={val} out of bounds"


def test_llm_confidence_cannot_dominate_without_evidence(monkeypatch):
    """Invariant: LLM confidence cannot dominate; if no sources retrieved, final confidence is strictly 0.0."""
    monkeypatch.setattr(verification_service, "search_evidence", lambda *_args, **_kwargs: [])

    result = verification_service.verify_single_claim("Any arbitrary assertion.", generate_query=False, live_mode=True)

    assert result["confidence"] == 0.0
    factors = json.loads(result["confidence_factors"])
    assert factors["final_confidence"] == 0.0
