"""Strict agent-facing contracts; generated JSON schema also powers MCP."""
from typing import Literal
from urllib.parse import urlparse
from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator

Style = Literal["podcast", "gaming", "education", "fitness", "cinematic", "campaign"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Campaign(StrictModel):
    name: str = Field(default="", max_length=200)
    platform: Literal["independent", "whop", "tryshoot", "other"] = "independent"
    audience: str = Field(default="", max_length=1000)
    objective: str = Field(default="", max_length=2000)
    required_phrases: list[str] = Field(default_factory=list, max_length=20)
    forbidden_phrases: list[str] = Field(default_factory=list, max_length=20)
    notes: str = Field(default="", max_length=4000)

    @field_validator("required_phrases", "forbidden_phrases")
    @classmethod
    def phrases(cls, values):
        if any(not value.strip() or len(value) > 200 for value in values):
            raise ValueError("Phrases must contain 1–200 characters")
        return [value.strip() for value in values]


class ProductionRequest(StrictModel):
    source_url: str = Field(default="", max_length=4096)
    upload_id: str = Field(default="", pattern=r"^[A-Za-z0-9-]{0,64}$")
    confirm_rights: Literal[True]
    style: Style = "podcast"
    campaign: Campaign = Field(default_factory=Campaign)
    max_clips: int = Field(default=15, ge=1, le=15)
    min_seconds: float = Field(default=15, ge=5, le=175)
    max_seconds: float = Field(default=60, ge=10, le=180)
    minimum_score: float = Field(default=70, ge=0, le=100)
    force_low_quality: bool = False
    captions: bool = True

    @model_validator(mode="after")
    def validate_source(self):
        if bool(self.source_url) == bool(self.upload_id):
            raise ValueError("Provide exactly one of source_url or upload_id")
        if self.source_url:
            parsed = urlparse(self.source_url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("source_url must be an HTTP(S) URL without credentials")
        if self.max_seconds < self.min_seconds + 5:
            raise ValueError("max_seconds must be at least 5 seconds above min_seconds")
        return self


class ClipRef(StrictModel):
    job_id: str = Field(pattern=r"^[A-Za-z0-9-]{1,64}$")
    clip_index: int = Field(ge=0, le=1000)


class ProductionRef(StrictModel):
    production_id: str = Field(pattern=r"^[A-Za-z0-9-]{1,64}$")


class SubmitProduction(ProductionRequest):
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")


def editorial_brief(request):
    from clipfac.presets import get_preset
    preset = get_preset(request.style)
    return {
        "schema_version": 1,
        "style": {"id": preset["id"], "version": preset["version"]},
        "direction": preset["direction"],
        "campaign": request.campaign.model_dump(),
        "selection": {
            "max_clips": request.max_clips,
            "minimum_score": request.minimum_score,
            "duration": [request.min_seconds, request.max_seconds],
            "rule": "Find distinct complete moments. A ceiling is not a quota. Never invent missing context or claims.",
        },
    }
