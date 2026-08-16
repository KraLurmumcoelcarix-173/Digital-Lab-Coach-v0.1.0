"""Reasoning-tier request shaping in the client wrapper.

claude-opus-5 thinks by default; at the provider-default effort it can
spend the ENTIRE max_tokens budget on thinking blocks, returning a reply
with zero text.
The wrapper therefore shapes opus-5 requests the same way its OpenAI
branch already shapes gpt-5 ones: bounded effort + a token floor so
thinking + text fit. These tests pin that at the SDK boundary."""

import pytest

import dlc.llm.client as lc


class _CaptureSDK:
    """Stands in for Anthropic(): records messages.create kwargs."""
    last_kwargs = None

    def __init__(self, *a, **kw):
        pass

    @property
    def messages(self):
        return self

    def create(self, **kw):
        _CaptureSDK.last_kwargs = kw

        class _Block:
            type = "text"
            text = "{\"ok\": true}"

        class _Usage:
            input_tokens = 10
            output_tokens = 20

        class _Resp:
            content = [_Block()]
            usage = _Usage()
            stop_reason = "end_turn"

        return _Resp()


@pytest.fixture()
def _capture(monkeypatch):
    _CaptureSDK.last_kwargs = None
    monkeypatch.setattr(lc, "Anthropic", _CaptureSDK, raising=False)
    monkeypatch.setattr(lc, "get_api_key", lambda p=None: "sk-test")
    return _CaptureSDK


def test_opus5_gets_bounded_effort_and_token_floor(_capture):
    resp = lc.call_llm("hi", model="claude-opus-5", max_tokens=2000)
    assert resp["ok"] is True
    kw = _capture.last_kwargs
    assert kw["output_config"] == {"effort": "low"}
    assert kw["max_tokens"] == 8000  # floor: thinking + text share the cap


def test_opus5_caller_effort_and_headroom_win(_capture):
    lc.call_llm("hi", model="claude-opus-5", max_tokens=16000,
                effort="medium")
    kw = _capture.last_kwargs
    assert kw["output_config"] == {"effort": "medium"}
    assert kw["max_tokens"] == 16000


def test_non_reasoning_models_keep_provider_defaults(_capture):
    lc.call_llm("hi", model="claude-opus-4-8", max_tokens=2000)
    kw = _capture.last_kwargs
    assert "output_config" not in kw
    assert kw["max_tokens"] == 2000


def test_sonnet5_is_shaped_like_the_reasoning_tier(_capture):
    # Claude 5-family models think by default — sonnet-5 gets the same
    # bounded effort + token floor that saved opus-5 from empty replies.
    resp = lc.call_llm("hi", model="claude-sonnet-5", max_tokens=2000)
    assert resp["ok"] is True
    kw = _capture.last_kwargs
    assert kw["output_config"] == {"effort": "low"}
    assert kw["max_tokens"] == 8000
