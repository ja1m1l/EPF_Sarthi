# EPF Sentinel — pytest unit tests for shared/gemini.py
#
# What is real vs mocked
# -----------------------
# * Real  : retry logic, _is_retryable classification, config constants.
# * Mocked: google-genai SDK client, boto3 Secrets Manager client.
# * No network, no live Gemini API calls, no AWS calls.

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch, PropertyMock
from types import SimpleNamespace

import pytest


# ─────────────────────────────────────────────────────────────
# Fixtures: reset module-scope caches before each test
# ─────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_gemini_caches(monkeypatch):
    """Reset module-level caches and set required env vars."""
    import shared.gemini as gmod
    gmod._api_key = None
    gmod._client = None
    monkeypatch.setenv("GEMINI_SECRET_NAME", "epf-sentinel/gemini-api-key")
    monkeypatch.setenv("AWS_REGION", "ap-south-1")
    yield
    gmod._api_key = None
    gmod._client = None


# ─────────────────────────────────────────────────────────────
# API key loading
# ─────────────────────────────────────────────────────────────

class TestApiKeyLoading:
    def test_loads_plain_string_secret(self, monkeypatch):
        import shared.gemini as gmod

        mock_sm = MagicMock()
        mock_sm.get_secret_value.return_value = {"SecretString": "test-api-key-123"}

        with patch("shared.gemini.boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_sm
            key = gmod._get_api_key()

        assert key == "test-api-key-123"
        mock_boto3.client.assert_called_once_with("secretsmanager", region_name="ap-south-1")

    def test_loads_json_secret_with_key_field(self, monkeypatch):
        import shared.gemini as gmod

        secret_json = json.dumps({"key": "json-api-key-456"})
        mock_sm = MagicMock()
        mock_sm.get_secret_value.return_value = {"SecretString": secret_json}

        with patch("shared.gemini.boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_sm
            key = gmod._get_api_key()

        assert key == "json-api-key-456"

    def test_caches_api_key(self, monkeypatch):
        import shared.gemini as gmod

        mock_sm = MagicMock()
        mock_sm.get_secret_value.return_value = {"SecretString": "cached-key"}

        with patch("shared.gemini.boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_sm
            key1 = gmod._get_api_key()
            key2 = gmod._get_api_key()

        assert key1 == key2 == "cached-key"
        # Only one call — second was from cache
        assert mock_sm.get_secret_value.call_count == 1

    def test_raises_if_no_env_var(self, monkeypatch):
        import shared.gemini as gmod
        monkeypatch.setenv("GEMINI_SECRET_NAME", "")

        with pytest.raises(RuntimeError, match="GEMINI_SECRET_NAME"):
            gmod._get_api_key()


# ─────────────────────────────────────────────────────────────
# Retry logic
# ─────────────────────────────────────────────────────────────

class TestRetryLogic:
    def test_daily_quota_429_is_not_retryable_path(self):
        from shared.gemini import _is_daily_quota

        exc = Exception("limit: 0 (free_tier per day)")
        assert _is_daily_quota(exc) is True

    def test_is_retryable_429(self):
        from shared.gemini import _is_retryable
        exc = Exception("rate limited")
        exc.code = 429
        assert _is_retryable(exc) is True

    def test_is_retryable_500(self):
        from shared.gemini import _is_retryable
        exc = Exception("server error")
        exc.code = 500
        assert _is_retryable(exc) is True

    def test_is_retryable_503(self):
        from shared.gemini import _is_retryable
        exc = Exception("unavailable")
        exc.code = 503
        assert _is_retryable(exc) is True

    def test_not_retryable_400(self):
        from shared.gemini import _is_retryable
        exc = Exception("bad request")
        exc.code = 400
        assert _is_retryable(exc) is False

    def test_not_retryable_401(self):
        from shared.gemini import _is_retryable
        exc = Exception("unauthorized")
        exc.code = 401
        assert _is_retryable(exc) is False

    def test_not_retryable_plain_exception(self):
        from shared.gemini import _is_retryable
        assert _is_retryable(ValueError("nope")) is False

    def test_retryable_by_class_name(self):
        from shared.gemini import _is_retryable

        class ResourceExhausted(Exception):
            pass

        assert _is_retryable(ResourceExhausted("quota")) is True

    def test_retry_succeeds_after_transient_failure(self, monkeypatch):
        from shared.gemini import _retry
        # Speed up test by removing sleep
        monkeypatch.setattr("shared.gemini.time.sleep", lambda _: None)

        call_count = 0

        def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                exc = Exception("transient")
                exc.code = 503
                raise exc
            return "success"

        result = _retry(flaky)
        assert result == "success"
        assert call_count == 2

    def test_retry_gives_up_after_max_retries(self, monkeypatch):
        from shared.gemini import _retry
        monkeypatch.setattr("shared.gemini.time.sleep", lambda _: None)

        def always_fail():
            exc = Exception("always 500")
            exc.code = 500
            raise exc

        with pytest.raises(Exception, match="always 500"):
            _retry(always_fail)

    def test_non_retryable_error_propagates_immediately(self, monkeypatch):
        from shared.gemini import _retry
        monkeypatch.setattr("shared.gemini.time.sleep", lambda _: None)

        call_count = 0

        def bad_request():
            nonlocal call_count
            call_count += 1
            exc = Exception("invalid input")
            exc.code = 400
            raise exc

        with pytest.raises(Exception, match="invalid input"):
            _retry(bad_request)
        # Should not have retried
        assert call_count == 1


# ─────────────────────────────────────────────────────────────
# embed() function
# ─────────────────────────────────────────────────────────────

class TestEmbed:
    def test_embed_returns_vectors(self, monkeypatch):
        import shared.gemini as gmod

        # Set up pre-cached key and client
        gmod._api_key = "test-key"
        mock_client = MagicMock()
        gmod._client = mock_client

        # Mock embed response
        emb1 = SimpleNamespace(values=[0.1, 0.2, 0.3])
        emb2 = SimpleNamespace(values=[0.4, 0.5, 0.6])
        mock_client.models.embed_content.return_value = SimpleNamespace(
            embeddings=[emb1, emb2]
        )

        result = gmod.embed(["text one", "text two"])

        assert len(result) == 2
        assert result[0] == [0.1, 0.2, 0.3]
        assert result[1] == [0.4, 0.5, 0.6]

        # Verify model name
        call_kwargs = mock_client.models.embed_content.call_args
        assert call_kwargs.kwargs["model"] == "gemini-embedding-001"

    def test_embed_empty_list(self, monkeypatch):
        import shared.gemini as gmod
        assert gmod.embed([]) == []

    def test_embed_retries_on_429_and_succeeds(self, monkeypatch):
        """Simulate one 429 followed by success; verify call_count == 2."""
        import shared.gemini as gmod
        monkeypatch.setattr("shared.gemini.time.sleep", lambda _: None)

        gmod._api_key = "test-key"
        mock_client = MagicMock()
        gmod._client = mock_client

        rate_limit_exc = Exception("Resource exhausted")
        rate_limit_exc.code = 429

        success_response = SimpleNamespace(
            embeddings=[SimpleNamespace(values=[0.1, 0.2, 0.3])]
        )
        mock_client.models.embed_content.side_effect = [rate_limit_exc, success_response]

        result = gmod.embed(["sample text"])

        assert result == [[0.1, 0.2, 0.3]]
        # Exactly 2 calls: 1 initial call (got 429) + 1 retry (succeeded)
        assert mock_client.models.embed_content.call_count == 2

    def test_embed_does_not_retry_on_400(self, monkeypatch):
        """Simulate a 400 Bad Request; verify call_count == 1 (no retry)."""
        import shared.gemini as gmod
        monkeypatch.setattr("shared.gemini.time.sleep", lambda _: None)

        gmod._api_key = "test-key"
        mock_client = MagicMock()
        gmod._client = mock_client

        bad_req_exc = Exception("Bad request")
        bad_req_exc.code = 400
        mock_client.models.embed_content.side_effect = bad_req_exc

        with pytest.raises(Exception, match="Bad request"):
            gmod.embed(["bad input"])

        # Exactly 1 call: 400 is not retryable
        assert mock_client.models.embed_content.call_count == 1


# ─────────────────────────────────────────────────────────────
# generate() function
# ─────────────────────────────────────────────────────────────

class TestGenerate:
    def test_generate_returns_text(self, monkeypatch):
        import shared.gemini as gmod

        gmod._api_key = "test-key"
        mock_client = MagicMock()
        gmod._client = mock_client

        mock_response = MagicMock()
        mock_response.text = "Generated response text"
        mock_response.usage_metadata = SimpleNamespace(
            prompt_token_count=10,
            candidates_token_count=5,
            total_token_count=15,
        )
        mock_client.models.generate_content.return_value = mock_response

        result = gmod.generate("test prompt")

        assert result == "Generated response text"
        call_kwargs = mock_client.models.generate_content.call_args
        assert call_kwargs.kwargs["model"] == gmod.MODEL_CONFIG["generation_model"]
        assert call_kwargs.kwargs["contents"] == "test prompt"

    def test_generate_retries_on_429_and_succeeds(self, monkeypatch):
        """Simulate one 429 followed by success; verify call_count == 2."""
        import shared.gemini as gmod
        monkeypatch.setattr("shared.gemini.time.sleep", lambda _: None)

        gmod._api_key = "test-key"
        mock_client = MagicMock()
        gmod._client = mock_client

        rate_limit_exc = Exception("Resource exhausted")
        rate_limit_exc.code = 429

        success_response = MagicMock()
        success_response.text = "Success after rate limit"
        success_response.usage_metadata = SimpleNamespace(
            prompt_token_count=5,
            candidates_token_count=5,
            total_token_count=10,
        )
        mock_client.models.generate_content.side_effect = [rate_limit_exc, success_response]

        result = gmod.generate("test prompt")

        assert result == "Success after rate limit"
        # Exactly 2 calls: 1 initial call (got 429) + 1 retry (succeeded)
        assert mock_client.models.generate_content.call_count == 2
