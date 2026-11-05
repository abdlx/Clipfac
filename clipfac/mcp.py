"""Additive tools. Keep the existing MCP transport and API authorization."""
from clipfac.models import ClipRef, ProductionRef, ProductionRequest, SubmitProduction, SubmitAnalysis, AnalysisRef, TranscriptWindow
from clipfac.plans import CreatePlan, RevisePlan, PlanRef, RenderPlan

# name -> (scope, method, endpoint, request model, description)
ROUTES = {
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
                     "Produce clips with a versioned style and campaign brief. Reuse idempotency_key for retries. Returns immediately; get_production reports progress and shortlisted/held clips. Up to 15 candidates per source."),
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
