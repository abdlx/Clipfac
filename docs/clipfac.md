# Clipfac production layer

Clipfac is a universal self-hosted video workspace and agent API built on
OpenShorts. HOB, Hermes, Claude, n8n or other MCP clients use the same tools.
New code in clipfac/ is MIT. Upstream notices and cloud/LICENSE remain intact.

## Install

1. Copy .env.clipfac.example to .env.
2. Generate an installation token:
   python -c "import secrets; print(secrets.token_urlsafe(32))"
3. Set CLIPFAC_ADMIN_TOKEN and GEMINI_API_KEY, or configure
   LLM_BASE_URL + LLM_MODEL + LLM_API_KEY. Vision stages still need Gemini.
4. Run docker compose -f compose.clipfac.yml up --build -d
5. Open http://localhost:8080 and connect the installation token.

The production profile exposes only 127.0.0.1:8080. Use an HTTPS reverse proxy
for remote agents. Keep frontend/API same-origin. The backend has no public
host port. The separate Remotion effects service is not included in this
profile; the upstream FFmpeg clipping/editor endpoints are available.

Defaults: one job, one clip worker, two FFmpeg threads, CPU int8 ASR.
Benchmark before increasing concurrency. No measured quality or speed gains
are claimed. Persistent volumes store output, uploads, models and SQLite.
Stop services before backing up all four for a consistent snapshot.
Upgrade: pull the desired commit and rebuild with the same compose command.
Rollback: check out the previous commit and rebuild, retaining all volumes.
Do not use compose down -v unless deliberately deleting all project data.

## Authentication and agents

CLIPFAC_AGENT_TOKENS accepts a JSON array:
[{"token":"GENERATE_A_SEPARATE_LONG_RANDOM_TOKEN","scopes":["read","process","edit"]}]

Scopes: read, process, edit, publish. Admin uses CLIPFAC_ADMIN_TOKEN.
These are trusted installation roles, not separate customer accounts.
The ledger isolates submissions by token identity. Access to upstream media
and jobs is shared across this one trusted installation.

Use Authorization: Bearer TOKEN on /mcp or REST. The dashboard exchanges a
token for a 12-hour HttpOnly SameSite=Strict cookie. Cookie mutations require
a matching Origin header. Installation tokens are not stored in browser storage.
Legacy editor provider keys retain upstream browser-storage behavior; use env
keys when provider secrets should not be stored in the browser.
Revoke an agent by removing its token from env and restarting.

## Production workflow

list_style_presets -> build_editorial_brief -> produce_clips ->
get_production -> get_clip_transcript / get_clip_scenes / inspect_clip ->
existing recut_clip / add_subtitles -> review -> publish_clip.

produce_clips accepts source_url OR upload_id, confirm_rights=true, style,
campaign, duration bounds, max_clips, minimum_score and idempotency_key.
For files use create_upload + PUT first. Reuse the same idempotency_key after
a lost response. A changed body with the same key returns 409.

SQLite persists requests, briefs and upstream job IDs. It does not replace the
upstream render queue/checkpoints. A crash between reserving and receiving a
job ID remains submitting/uncertain, never blindly replayed. Reconcile against
the instance's jobs before creating a new request.

The six explicit styles configure caption typography, emphasis, hooks and layout
options in each rendering subprocess. The gallery shows typography concepts,
not actual video previews. Automatic multimodal style inference is not implemented.

Campaign data informs selection. Required/forbidden phrases are literal transcript
checks, not visual compliance certification. The user/agent supplies the brief;
there is no Whop/Tryshoot scraping or authenticated campaign integration.

max_clips is a ceiling of up to 15 candidates per source, never a forced quota.
The shortlist holds low/missing scores, invalid durations, substantial overlap
and phrase violations while preserving original clip indexes. Generated clips
remain accessible in the original editor. Scores do not guarantee views.

inspect_clip runs bounded ffprobe and FFmpeg decoding: streams, resolution,
black intervals, audio presence and silence/low volume. It does not certify
caption accuracy/overflow, subject framing, editorial coherence or campaign
visual compliance. No autonomous repair loop or real-media benchmark is claimed.

## REST

GET /api/clipfac/presets
POST /api/clipfac/brief
POST /api/clipfac/productions
GET /api/clipfac/productions
GET /api/clipfac/productions/{production_id}
POST /api/clipfac/inspect
POST /api/clipfac/session
DELETE /api/clipfac/session

BILLING_ENABLED=0 is required for the standalone layer. No cloud billing code
was copied into it.

## Checks

pytest tests/ -v
cd dashboard && npm ci && npm run build && npm run lint

The new regression suite checks contracts, retries, ambiguous submissions,
token isolation, REST/MCP publish scope guards, cookie CSRF, shortlist filtering,
preset isolation and silence detection. Validate real media and GPU behavior
separately on the deployment hardware.


## Staged analysis and immutable plans

The workspace now defaults to analysis before rendering:

1. analyze_video ingests the source, transcribes, selects candidates, samples
   eight real frames and generates a contact sheet. It saves a source SHA-256.
2. The director combines transcript and frames to choose a style, layout and
   mood. Explicit user styles override its recommendation. A missing/failing
   vision provider is reported as an explicit fallback with zero confidence.
3. create_edit_plan filters candidates and pins exact integer-millisecond
   ranges, titles, hooks, source fingerprint, preset snapshot and descriptions.
4. revise_edit_plan creates a new immutable revision. Stale head/revision writes
   return 409; previous plans remain available.
5. render_edit_plan reuses the source and transcript. It verifies source identity
   before queueing and again in the worker, and compiles chosen cuts without
   rerunning transcription or moment selection.

Source contact sheets are storyboard previews, not rendered motion previews.
Output framing still uses upstream scene/subject tracking; byte-identical
encoded video across hardware/library versions is not promised. Pin the image
and dependencies for reproducible deployment. Preset changes require creating
a new revision before rendering an older plan.

New REST endpoints:
POST /api/clipfac/analyses
GET /api/clipfac/analyses/{analysis_job_id}
GET /api/clipfac/analyses/{analysis_job_id}/transcript?start_ms=0&end_ms=300000
POST /api/clipfac/plans
GET /api/clipfac/plans/{plan_id}
POST /api/clipfac/plans/revise
POST /api/clipfac/plans/render

For any OpenAI-compatible vision-capable endpoint set:
CLIPFAC_VISION_BASE_URL=https://provider.example/v1
CLIPFAC_VISION_MODEL=your-vision-model
CLIPFAC_VISION_API_KEY=your-key

If no separate vision endpoint is configured, the director uses Gemini and
CLIPFAC_DIRECTOR_GEMINI_MODEL (or GEMINI_MODEL). These are real capability
settings: a text-only model is never treated as a vision model. Cost values are
unknown unless priced; returned usage is not presented as free.

The scoped tokens remain installation roles; they are not tenant accounts.
Source/media retention still applies: expired source files prevent rendering.
The render compiler currently supports one continuous source interval per clip.
Use the existing recut tool for more complex concatenated edits.
