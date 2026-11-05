"""Additive tools. Keep the existing MCP transport and API authorization."""
from clipfac.models import ClipRef, ProductionRef, ProductionRequest, SubmitProduction, SubmitAnalysis, AnalysisRef, TranscriptWindow, DeliveryRetry, JobRef, PublishStatusRef
from clipfac.plans import CreatePlan, RevisePlan, PlanRef, RenderPlan
from clipfac.reviews import ReviewRequest, ReviewRef
from clipfac.publishing import PublishRequest

# name -> (scope, method, endpoint, request model, description)
ROUTES = {
    "get_publish_status": ("read", "GET", "/api/social/post-status/{request_id}", PublishStatusRef,
                           "Check the asynchronous Upload-Post request returned by publish_reviewed_clip. Submitted does not mean a platform has finished publishing."),
    "list_social_profiles": ("read", "GET", "/api/social/user", None,
                             "List profiles and connected platforms on this installation's Upload-Post account. Select an explicit user_id for publishing when more than one profile exists."),
    "publish_reviewed_clip": ("publish", "POST", "/api/clipfac/publish", PublishRequest,
                              "Publish a reviewed clip with persistent idempotency. Production requires passing review of current render bytes. Reuse the same key after lost responses; uncertain outcomes are never resent."),
    "get_provider_status": ("read", "GET", "/api/clipfac/providers", None,
                            "Read configured text, vision, Gemini and media provider status. Keys are never returned. Configured status does not certify a successful provider connection."),
    "review_clip": ("read", "POST", "/api/clipfac/reviews", ReviewRequest,
                    "Run technical checks and sampled-frame editorial review, storing an immutable report tied to the rendered file hash. Model failures never count as a pass. retry_failed explicitly retries a failed provider call."),
    "get_clip_review": ("read", "GET", "/api/clipfac/reviews/{review_id}", ReviewRef,
                        "Read a persisted visual/technical report, evidence contact sheet, limitations and proposed typed repairs."),
    "plan_clip_repairs": ("edit", "POST", "/api/clipfac/reviews/repair", ReviewRef,
                          "Create an immutable single-clip repair plan from a completed current review. Only bounded trims, preset changes and source-quote hooks are allowed. Campaign constraints are rechecked. Maximum two repair plans per source/campaign/candidate; render separately and review again."),
    "get_webhook_deliveries": ("read", "GET", "/api/clipfac/webhooks/{job_id}", JobRef,
                              "Read persisted webhook attempts, delivery IDs, pending/delivered/dead-letter states. No credentials or callback secrets are returned."),
    "retry_webhook_delivery": ("admin", "POST", "/api/clipfac/webhooks/retry", DeliveryRetry,
                               "Requeue a dead-letter webhook with its original stable delivery ID. Requires installation admin access."),
    "analyze_video": ("process", "POST", "/api/clipfac/analyses", SubmitAnalysis,
                      "Download/ingest once, transcribe, find candidate moments and inspect sampled frames for style/mood. Does not render clips. style=auto chooses a versioned look, with explicit fallback warnings. Returns a submission; poll get_production."),
    "get_analysis": ("read", "GET", "/api/clipfac/analyses/{analysis_job_id}", AnalysisRef,
                     "Read retained source identity, candidate moments, contact sheet and automatic direction. Transcript is read separately in bounded windows."),
    "read_analysis_transcript": ("read", "GET", "/api/clipfac/analyses/{analysis_job_id}/transcript", TranscriptWindow,
                                 "Read at most five minutes of timestamped transcript from an analysis."),
    "create_edit_plan": ("edit", "POST", "/api/clipfac/plans", CreatePlan,
                         "Compile passing source candidates into an immutable plan with exact integer millisecond cuts and pinned style. Does not render."),
    "get_edit_plan": ("read", "GET", "/api/clipfac/plans/{plan_id}", PlanRef,
                      "Read an immutable plan revision, source fingerprint, render digest, latest revision ID and source contact sheet."),
    "revise_edit_plan": ("edit", "POST", "/api/clipfac/plans/revise", RevisePlan,
                         "Create a new immutable revision. expected_revision and plan_id must name the current head; stale edits return 409. Old revisions stay unchanged."),
    "render_edit_plan": ("process", "POST", "/api/clipfac/plans/render", RenderPlan,
                         "Render an exact retained-source edit plan. Reuses transcript and candidate selection; checks source fingerprint and pinned preset. Reuse the same idempotency key on retries. Does not publish."),
    "list_style_presets": ("read", "GET", "/api/clipfac/presets", None,
                          "List six versioned editing looks and their actual render settings. Choose a style based on inspected footage; Automatic style inference is available through analyze_video."),
    "build_editorial_brief": ("read", "POST", "/api/clipfac/brief", ProductionRequest,
                             "Validate source, style and campaign requirements before spending processing resources. Does not download or analyze footage."),
    "produce_clips": ("process", "POST", "/api/clipfac/productions", SubmitProduction,
                     "Produce clips with a versioned style and campaign brief. Reuse idempotency_key for retries. Returns immediately; get_production reports progress and shortlisted/held clips. Up to 60 candidates per source, with bounded long-source detail batches."),
    "get_production": ("read", "GET", "/api/clipfac/productions/{production_id}", ProductionRef,
                      "Get production progress and quality shortlist. Scores are estimates. Technical and editorial review remain separate."),
    "list_productions": ("read", "GET", "/api/clipfac/productions", None,
                        "List the latest 30 production submissions for this token identity."),
    "inspect_clip": ("read", "POST", "/api/clipfac/inspect", ClipRef,
                     "Probe and decode a retained clip, flag black intervals and silent/quiet audio. Does not assess caption accuracy, subject framing or editorial quality."),
    "get_clip_transcript": ("read", "GET", "/api/clip/{job_id}/{clip_index}/transcript", ClipRef,
                            "Read a clip's timestamped words for editorial review and precise recutting."),
    "get_clip_scenes": ("read", "GET", "/api/clip/{job_id}/{clip_index}/scenes", ClipRef,
                        "Read scene and framing metadata from the existing editor."),
}
TOOL_SCOPES = {name: definition[0] for name, definition in ROUTES.items()}


def register(server):
    from pydantic import ValidationError
    for name, (scope, method, path, model, description) in ROUTES.items():
        server.TOOLS.append({
            "name": name, "title": name.replace("_", " ").title(), "description": description,
            "inputSchema": model.model_json_schema() if model else {"type": "object", "properties": {}, "additionalProperties": False},
            "annotations": {"readOnlyHint": scope == "read", "destructiveHint": False,
                            "openWorldHint": name in ("produce_clips", "analyze_video"),
                            "idempotentHint": name != "create_edit_plan"},
        })

        def make_handler(method, path, model):
            async def handler(client, args):
                try:
                    if model:
                        data = model.model_validate(args).model_dump()
                    elif args:
                        return {"error": "This tool accepts no arguments"}, True
                    else:
                        data = {}
                except ValidationError as exc:
                    return {"error": "Invalid tool arguments", "details": exc.errors(include_url=False, include_context=False)}, True
                endpoint = path.format(**data)
                query = {key: value for key, value in data.items()
                         if "{" + key + "}" not in path} if method == "GET" else None
                response = await client.request(method, endpoint, params=query,
                                                json=data if method == "POST" else None)
                if response.status_code >= 400:
                    return server._api_error(response), True
                return response.json(), False
            return handler
        server._TOOL_IMPLS[name] = make_handler(method, path, model)
