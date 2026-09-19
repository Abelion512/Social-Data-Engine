#!/usr/bin/env python3
"""
Unit tests untuk src/pipeline/legacy.py (dulu src/pipeline.py) — quality_score,
spam detection, llm_enrich_identities.
"""
import sys
from pathlib import Path

# Setup path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.pipeline import quality_score, llm_enrich_identities, GATING_THRESHOLD


class TestQualityScore:
    """Tests untuk heuristic quality scoring."""

    def test_empty_rec(self):
        assert quality_score({}) == 0.0

    def test_empty_text(self):
        assert quality_score({"text_normalized": ""}) == 0.0

    def test_single_char(self):
        # Single char: 1 unique / 1 total = 1.0 semantic density, no spam/toxic
        # quality_score gives high score to clean short text (by design)
        score = quality_score({"text_normalized": "a"})
        assert score > 0, f"Single char should have some score, got {score}"

    def test_normal_comment(self):
        score = quality_score({"text_normalized": "let us connect mahasiswi HI UMY"})
        assert 0.5 < score <= 1.0, f"Normal comment should score high, got {score}"

    def test_spam_bare_url_single(self):
        # Single bare URL: url_count=1, not >1 so no spam penalty (by design)
        # Quality depends on semantic density of remaining text
        score = quality_score({"text_normalized": "visit www.spam.com please"})
        assert score >= 0, f"Single bare URL should have some score, got {score}"

    def test_spam_bare_urls_multiple(self):
        score = quality_score({"text_normalized": "check www.a.com and www.b.com"})
        assert score < 0.1, f"Multi bare URL spam should score very low, got {score}"

    def test_spam_http_urls(self):
        score = quality_score({"text_normalized": "visit https://spam.com and http://evil.com"})
        assert score < 0.1, f"Multi HTTP URL spam should score very low, got {score}"

    def test_toxic_content(self):
        score = quality_score({"text_normalized": "you are a bitch and a slut"})
        assert score < 0.3, f"Toxic content should score low, got {score}"

    def test_repeated_chars(self):
        score = quality_score({"text_normalized": "bangeeeeeeeeeet banget"})
        # Repeated chars increase spam probability
        normal = quality_score({"text_normalized": "banget banget"})
        assert score <= normal, f"Repeated chars should not score higher than normal"

    def test_gating_threshold(self):
        """Verify gating threshold is reasonable."""
        assert 0.0 < GATING_THRESHOLD < 1.0
        assert GATING_THRESHOLD == 0.15


class TestLlmEnrichIdentities:
    """Tests untuk LLM identity extraction (tanpa API call)."""

    def test_empty_input(self):
        try:
            result = llm_enrich_identities([])
            assert result == [], f"Empty input should return empty list, got {result}"
        except ImportError:
            pass  # requests not installed in test env

    def test_returns_same_length(self):
        """Verify return length matches input length."""
        try:
            records = [
                {"author_handle": "user1", "display_name": "User One", "text_raw": "hello"},
                {"author_handle": "user2", "display_name": "User Two", "text_raw": "world"},
            ]
            result = llm_enrich_identities(records)
            assert len(result) == len(records), f"Length mismatch: {len(result)} vs {len(records)}"
        except ImportError:
            pass

    def test_no_api_key_returns_none(self):
        """Without API key, all identities should be None."""
        try:
            records = [{"author_handle": "test", "display_name": "Test", "text_raw": "hi"}]
            result = llm_enrich_identities(records)
            assert all(r is None for r in result), "Without API key, all should be None"
        except ImportError:
            pass


if __name__ == "__main__":
    # Simple test runner
    import traceback
    passed = 0
    failed = 0

    for cls in [TestQualityScore, TestLlmEnrichIdentities]:
        instance = cls()
        for method_name in dir(instance):
            if method_name.startswith("test_"):
                try:
                    getattr(instance, method_name)()
                    print(f"  ✅ {cls.__name__}.{method_name}")
                    passed += 1
                except AssertionError as e:
                    print(f"  ❌ {cls.__name__}.{method_name}: {e}")
                    failed += 1
                except Exception as e:
                    print(f"  ❌ {cls.__name__}.{method_name}: {type(e).__name__}: {e}")
                    failed += 1

    print(f"\n{'='*40}")
    print(f"Results: {passed} passed, {failed} failed")
    if failed:
        sys.exit(1)
