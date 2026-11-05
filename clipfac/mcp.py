"""Additive tools. Keep the existing MCP transport and API authorization."""
from clipfac.models import ClipRef, ProductionRef, ProductionRequest, SubmitProduction

# name -> (scope, method, endpoint, request model, description)
ROUTES = {
    "list_style_presets": ("read", "GET", "/api/clipfac/presets", None,
                          "List six versioned editing looks and their actual render settings. Choose a style based on inspected footage; automatic style inference is not implemented."),
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
                            "openWorldHint": name == "produce_clips", "idempotentHint": True},
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
                response = await client.request(method, endpoint, json=data if method == "POST" else None)
                if response.status_code >= 400:
                    return server._api_error(response), True
                return response.json(), False
            return handler
        server._TOOL_IMPLS[name] = make_handler(method, path, model)
