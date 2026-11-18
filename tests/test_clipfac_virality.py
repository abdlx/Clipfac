"""Four-dimension virality scoring (SupoClip's dimensions, Clipfac's pipeline).

Covers: the detail prompt's virality contract, normalize_clip_virality
(clamp + recompute + graceful fallback), the DetailResponse schema round
trip, and the stored clip record carrying the breakdown.
"""
import json

import pytest
from pydantic import ValidationError

import gemini_worker
from gemini_worker import (
    DetailClipModel,
    DetailResponse,
    normalize_clip_virality,
)


def _clip(**overrides):
    clip = {
        "start": 12.5,
        "end": 41.0,
        "source_window_id": "w1",
        "predicted_score": 88,
        "video_description_for_tiktok": "t",
        "video_description_for_instagram": "i",
        "video_title_for_youtube_short": "The one email that almost ruined me",
        "viral_hook_text": "Stop doing this.",
        "why": "A bold claim with a payoff.",
    }
    clip.update(overrides)
    return clip


def _detail_prompt(**overrides):
    fmt = {
        "video_duration": 600,
        "language": "English",
        "windows_json": "[]",
        "min_clips": 6,
        "max_clips": 20,
        "min_secs": 15,
        "max_secs": 60,
        "performance_feedback": "",
    }
    fmt.update(overrides)
    return gemini_worker.DETAIL_PROMPT_TEMPLATE.format(**fmt)


def test_prompt_contains_virality_contract():
    prompt = _detail_prompt()
    for field in ("hook_score", "engagement_score", "value_score",
                  "shareability_score", "total_score", "hook_type",
                  "virality_reasoning"):
        assert field in prompt
    assert "four 0-25" in prompt
    assert "total_score" in prompt and "sum of the four sub-scores" in prompt
    for hook_type in ("question", "statement", "statistic", "story", "contrast", "none"):
        assert hook_type in prompt
    assert "predicted_score" in prompt and "MUST equal" in prompt
    # Score template untouched: virality is a detail-stage contract only.
    assert "virality" not in gemini_worker.SCORE_PROMPT_TEMPLATE


def test_prompt_with_no_performance_data_is_unchanged_in_meaning():
    prompt = _detail_prompt(performance_feedback="")
    assert "Past top-performing" not in prompt
    assert "Bias toward moments" not in prompt
    # The virality contract is part of the prompt regardless of feedback.
    assert "VIRALITY SCORING" in prompt


def test_normalize_recomputes_total_and_overwrites_predicted_score():
    clip = _clip(virality={
        "hook_score": 20, "engagement_score": 22, "value_score": 18,
        "shareability_score": 21, "total_score": 95,  # wrong arithmetic
        "hook_type": "statistic", "virality_reasoning": "A big number up front.",
    })
    normalize_clip_virality(clip)
    assert clip["virality"] == {
        "hook_score": 20, "engagement_score": 22, "value_score": 18,
        "shareability_score": 21, "total_score": 81,
        "hook_type": "statistic", "virality_reasoning": "A big number up front.",
    }
    assert clip["predicted_score"] == 81  # == total_score, not the model's 95


def test_normalize_clamps_subscores_to_0_25():
    clip = _clip(virality={
        "hook_score": 30, "engagement_score": -5, "value_score": 25,
        "shareability_score": 24, "total_score": 999,
        "hook_type": "story", "virality_reasoning": "",
    })
    normalize_clip_virality(clip)
    assert clip["virality"]["hook_score"] == 25
    assert clip["virality"]["engagement_score"] == 0
    assert clip["virality"]["total_score"] == 25 + 0 + 25 + 24
    assert clip["predicted_score"] == 74


def test_normalize_falls_back_when_subscores_absent():
    clip = _clip()  # no virality key at all (old result / model omitted it)
    normalize_clip_virality(clip)
    assert clip["virality"]["hook_score"] is None
    assert clip["virality"]["engagement_score"] is None
    assert clip["virality"]["value_score"] is None
    assert clip["virality"]["shareability_score"] is None
    assert clip["virality"]["total_score"] == 88
    assert clip["predicted_score"] == 88  # unchanged
    assert clip["virality"]["hook_type"] is None


def test_normalize_falls_back_on_partial_and_non_numeric_subscores():
    partial = _clip(predicted_score=70, virality={
        "hook_score": 20, "engagement_score": "high",
        "value_score": None, "shareability_score": 15,
        "hook_type": "question", "virality_reasoning": "Opens with a question.",
    })
    normalize_clip_virality(partial)
    assert partial["virality"]["hook_score"] is None  # all null when incomplete
    assert partial["virality"]["total_score"] == 70
    assert partial["predicted_score"] == 70
    assert partial["virality"]["hook_type"] == "question"


def test_normalize_rejects_invalid_hook_type_and_truncates_reasoning():
    clip = _clip(virality={
        "hook_score": 10, "engagement_score": 10, "value_score": 10,
        "shareability_score": 10, "total_score": 40,
        "hook_type": "clickbait", "virality_reasoning": "x" * 600,
    })
    normalize_clip_virality(clip)
    assert clip["virality"]["hook_type"] is None
    assert clip["virality"]["total_score"] == 40
    assert len(clip["virality"]["virality_reasoning"]) == 500


def test_normalize_is_idempotent():
    clip = _clip(virality={
        "hook_score": 20, "engagement_score": 20, "value_score": 20,
        "shareability_score": 20, "total_score": 80,
        "hook_type": "contrast", "virality_reasoning": "Before vs after.",
    })
    first = normalize_clip_virality(clip)
    second = normalize_clip_virality(clip)
    assert first == second
    assert clip["predicted_score"] == 80


def test_detail_response_round_trip_with_and_without_virality():
    with_virality = DetailResponse.model_validate({"shorts": [_clip(virality={
        "hook_score": 20, "engagement_score": 22, "value_score": 18,
        "shareability_score": 21, "total_score": 81,
        "hook_type": "statistic", "virality_reasoning": "A big number up front.",
    })]})
    dumped = with_virality.model_dump()
    assert dumped["shorts"][0]["virality"]["total_score"] == 81
    assert dumped["shorts"][0]["predicted_score"] == 88  # schema keeps both;
    # normalization (not the schema) is what makes them equal.
    normalized = normalize_clip_virality(dumped["shorts"][0])
    assert normalized["predicted_score"] == normalized["virality"]["total_score"] == 81

    # The four sub-scores are REQUIRED now: when they were optional the
    # small model omitted the whole object and every clip came back with
    # null sub-scores. Omitting virality, or any sub-score inside it, is a
    # schema error — the structured-output API enforces it before the
    # response reaches us, and the raw-JSON fallback in the caller still
    # saves a clip whose model skipped the object (normalize nulls it).
    with pytest.raises(ValidationError):
        DetailResponse.model_validate({"shorts": [_clip()]})
    with pytest.raises(ValidationError):
        DetailResponse.model_validate({"shorts": [_clip(virality={
            "hook_score": 20, "engagement_score": 22, "value_score": 18,
            "total_score": 60, "hook_type": "statistic",
            "virality_reasoning": "Missing shareability.",
        })]})


def test_detail_clip_model_rejects_nothing_for_out_of_range_subscore():
    # Out-of-range sub-scores must not fail schema validation (the clip
    # would be lost); clamping happens in normalize_clip_virality.
    model = DetailClipModel.model_validate(_clip(virality={
        "hook_score": 99, "engagement_score": -1, "value_score": 25,
        "shareability_score": 25, "total_score": 148,
        "hook_type": "story", "virality_reasoning": "",
    }))
    assert model.virality.hook_score == 99
    normalized = normalize_clip_virality(model.model_dump())
    assert normalized["virality"]["hook_score"] == 25
    assert normalized["virality"]["total_score"] == 25 + 0 + 25 + 25


def test_stored_clip_record_carries_four_subscores_plus_total(tmp_path):
    # The stored shape is the metadata.json shorts entry: the same dict the
    # job result clips and the API return. A JSON round trip must keep it.
    clip = normalize_clip_virality(_clip(virality={
        "hook_score": 20, "engagement_score": 22, "value_score": 18,
        "shareability_score": 21, "total_score": 81,
        "hook_type": "statistic", "virality_reasoning": "A big number up front.",
    }))
    path = tmp_path / "video_metadata.json"
    path.write_text(json.dumps({"shorts": [clip]}, indent=2))
    stored = json.loads(path.read_text())["shorts"][0]
    assert stored["virality"] == {
        "hook_score": 20, "engagement_score": 22, "value_score": 18,
        "shareability_score": 21, "total_score": 81,
        "hook_type": "statistic", "virality_reasoning": "A big number up front.",
    }
    assert stored["predicted_score"] == stored["virality"]["total_score"]
