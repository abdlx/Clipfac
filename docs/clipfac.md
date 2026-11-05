# Clipfac

Clipfac is a universal self-hosted production layer on OpenShorts. HOB, Hermes,
Claude, n8n and other MCP clients use the same workspace and API. The current
branch is experimental and unverified by executable CI or real-media testing.

## Installation and storage

1. Copy .env.clipfac.example to .env.
2. Generate a token:
   python -c "import secrets; print(secrets.token_urlsafe(32))"
3. Set CLIPFAC_ADMIN_TOKEN.
4. Run docker compose -f compose.clipfac.yml up --build -d
5. Open http://localhost:8080 and connect the installation token.
6. Configure providers in Provider connections.

Only 127.0.0.1:8080 is exposed. Frontend and API share an origin. For remote
clients put an HTTPS reverse proxy in front; no backend port is public.
Default processing uses one job, one clip worker, two FFmpeg threads and CPU
int8 Whisper. Benchmark before increasing concurrency. The optional separate
Remotion effects service is not included in this production profile.

Named volumes retain output, uploads, models and data. Stop services before
backing up all volumes for a consistent snapshot. The data backup MUST include
clipfac-vault.key as well as SQLite. A database backup without its matching key
cannot restore stored provider credentials. CLIPFAC_VAULT_KEY optionally
supplies an external Fernet key instead of the local 0600 key file.

To upgrade, check out the desired commit and rebuild with the same compose file.
To roll back, check out the previous code and retain volumes. This experimental
branch has no stable migration guarantee. Do not use compose down -v unless
intentionally deleting all projects and stored credentials.

## Providers

Provider connections saves keys encrypted with Fernet; raw keys are not returned.
The local key file and database are private runtime data, excluded from Git and
the build context. The entire trusted host must still be protected: local
encryption is not protection against an attacker who has both key and database.

Supported settings:
- Gemini: native text selection and upstream vision/layout stages.
- Compatible text endpoint: LLM_BASE_URL / LLM_MODEL / LLM_API_KEY.
- Compatible vision endpoint: CLIPFAC_VISION_BASE_URL / MODEL / API_KEY.
- Upload-Post, including a default profile username.
- ElevenLabs and fal.ai for the existing editor features.

Compatible vision currently powers direction and sampled review; it does not
replace every upstream Gemini-specific layout/silent-video implementation.
Configured status means settings exist, not that a live provider test passed.
The native Gemini director model can be set with CLIPFAC_DIRECTOR_GEMINI_MODEL.

Saving settings affects future subprocesses. Running jobs keep their environment
snapshot. Provider defaults are injected server-side for existing media endpoints.
Deleting an override restores deployment environment values; disabling an
override disables that configuration. Browser-provided legacy Gemini keys remain
supported and take precedence when explicitly present in the legacy editor.
Future provider keys should be saved centrally rather than in browser storage.

Model usage is reported when available. Unknown provider prices are not treated
as zero-cost usage.

## Agent roles and shared workspace

CLIPFAC_AGENT_TOKENS accepts:
[{"token":"GENERATE_A_SEPARATE_LONG_RANDOM_TOKEN","scopes":["read","process","edit"]}]

Scopes: read, process, edit, publish. Installation admin uses CLIPFAC_ADMIN_TOKEN.
These are roles in ONE trusted workspace, not isolated tenant accounts.
Plans, reports and submissions are shared so editing, QA and publishing agents
can hand work to each other. The default workspace ID is local; it can be set
with CLIPFAC_WORKSPACE_ID.

Suggested role scopes:
- Research/QA: read.
- Editing: read, process, edit.
- Publishing: read, publish.
- Orchestrator: scopes needed for its assigned work; provider changes stay admin.

Use Authorization: Bearer TOKEN on /mcp and REST. Browser login exchanges the
installation token for a 12-hour HttpOnly SameSite=Strict cookie. Cookie-backed
writes require a matching Origin header. Provider keys never need to be shared
among agents. Revoking an agent means removing its token and restarting.

For stdio, run python mcp_stdio.py and pass the role token using OPENSHORTS_API_KEY
(the retained upstream environment name). Stdio and HTTP share tool code.

## Staged production

1. analyze_video ingests once, transcribes, ranks candidates, samples eight
   source frames, saves a real contact sheet, and records the source SHA-256.
   No clips are rendered.
2. The director combines transcript and frames to choose a style, layout, mood,
   preservation priorities and things to avoid. Explicit style choices win.
   Provider failure is an explicit fallback with zero confidence, never a
   confident automatic decision.
3. create_edit_plan selects passing candidates and pins integer-millisecond cuts,
   titles, hooks, source fingerprint and a preset snapshot.
4. revise_edit_plan creates an immutable revision. Stale head/revision updates
   return 409. Previous plans stay readable.
5. render_edit_plan reuses source and transcript. It verifies source identity
   before queueing and in the worker, without another download/selection pass.

The six styles are podcast, gaming, education, fitness, cinematic and campaign.
They configure actual caption typography, emphasis, hooks and layout options.
Source contact sheets are storyboard previews, not motion previews. Preset
changes require revising a plan before rendering it with changed settings.
Framing still uses upstream scene/subject tracking. Pin images and dependencies
for reproducibility; cross-hardware byte-identical video is not promised.

The candidate ceiling is 1–60. Defaults remain 15. For larger budgets the
transcript pipeline inspects more shortlisted windows in bounded detail batches.
A ceiling is never a quota: weak, repetitive or context-dependent material can
produce fewer candidates or none. Upstream process_video retains its legacy
15-clip target; the staged Clipfac workflow supports the larger budget.

Campaign JSON informs selection. Required/forbidden phrases are literal
transcript checks, not visual or legal compliance certification. The user/agent
supplies campaign rules; authenticated Whop/Tryshoot fetching is not included.
Plans currently support one continuous source interval per clip. More complex
montages can use the existing recut editor; such legacy edits require a new
source plan before automatic repair.

Read full transcripts in windows of at most five minutes to bound tool output.

## Reviews and bounded repairs

inspect_clip performs bounded ffprobe/FFmpeg checks for playable streams,
resolution, black intervals, missing audio, silence and quiet audio.

review_clip adds sampled-frame review using a vision provider, caption transcript
and source-plan context. Reports persist with the media hash and model identity.
Cached reports are reused for the same bytes; failed provider calls require
retry_failed=true to retry. Errors, absent frames, low confidence, unresolved
major findings and manual-review items never count as a pass.

A passing decision is named sampled_checks_passed. It is not proof that every
frame is correct or that a video will perform. Still frames do not certify
continuous pacing or audio aesthetics. Findings cite sampled frames; unobserved
material remains uncertain.

plan_clip_repairs creates a new single-clip plan, preserving the original render:
- Trim at most two seconds from either boundary, within the brief.
- Change to an existing versioned style.
- Replace a hook only with a verbatim retained-transcript quote.
- Recheck duration and campaign phrases.

There are at most two repair plans per source/campaign/candidate. The default can
be lowered with CLIPFAC_REPAIR_LIMIT. Unsupported fixes require manual editing.
Render the new plan and review it again; creating a repair plan does not itself
spend render resources or publish anything.

## Publishing

The production compose file sets CLIPFAC_REQUIRE_REVIEW=1. Clip publishing
requires the newest review of the exact current media bytes to pass. A newer
failure or finding revokes an older pass. QA and publishing can use different
role tokens. Editing a render invalidates its old approval.

Prefer publish_reviewed_clip, which persists an idempotency record before calling
Upload-Post. Reuse the same key after connection failures. An uncertain platform
outcome is never blindly resent. Submitted means accepted for asynchronous
processing, not published: use get_publish_status.

Choose user_id explicitly or configure the default Upload-Post profile. A single
connected profile can be selected automatically; multiple profiles require an
explicit choice. Platform/account requirements still apply.

An installation admin can deliberately override the review gate with
X-Clipfac-Review-Override: 1 on a direct API request. Ordinary scoped publishing
agents cannot. This override is not an MCP repair or publishing parameter.

## Durable callbacks

Staged analysis/render requests accept webhook_url and webhook_secret. Targets
retain upstream public-address restrictions, checked again at connect time.
Private LAN callbacks are not supported; local agents can poll MCP/REST.

Terminal callbacks persist before dropping recovery manifests. The outbox uses
leases, exponential backoff and dead-letter states, surviving process restarts.
Delivery is AT LEAST ONCE. Deduplicate using delivery_id, which is stable across
retries and manual dead-letter replay. Never assume exactly-once HTTP delivery.

Signatures:
- X-OpenShorts-Signature: legacy sha256 HMAC over body.
- X-Clipfac-Signature: v1 HMAC over timestamp + "." + body.
- X-Clipfac-Timestamp and X-Clipfac-Delivery-ID accompany each attempt.

Verify signature and freshness in the receiver, and persist delivery IDs before
side effects. Callback secrets are private runtime data. Stored submission
arguments retain only their secret digest, not plaintext callback secrets.
get_webhook_deliveries shows status without revealing secrets. Dead-letter
replay requires installation admin.

## Reliability and limits

Submission and publishing ledgers use idempotency keys. A different body with
the same key returns 409. Crashes between reservation and receiving an upstream
job ID remain submitting/uncertain and are not blindly replayed. Reconcile with
instance/platform status before using a new key.

The ledger supplements upstream queue/checkpoint recovery. It is not a distributed
render scheduler. Source/media retention still applies. Reports, plans and
callback histories are retained in SQLite; media expiry does not erase the ledger.

Automatic complex crop correction, generated B-roll, sound design, arbitrary
timeline effects and a real-media quality benchmark remain unfinished. No
10x quality/speed result is claimed without measurements.

## Validation

pytest tests/ -v
cd dashboard && npm ci && npm run build && npm run lint

CI targets backend tests plus frontend build/lint on main, this feature branch
and PRs. The new suites cover strict contracts, staged source reuse, immutable
revisions, source hashes, credentials, scoped/shared roles, reviews, repair
budgets, publishing idempotency and webhook leases. They still need execution
in an available runtime, followed by Docker and real-video benchmarking.


## Real-media benchmarking

CI now installs FFmpeg and includes synthetic moving-picture/audio, black-frame,
digital-silence, missing-audio, corrupt-media and contact-sheet tests. These
verify implemented technical signals; they do not rate professional editing.

To evaluate the actual pipeline, copy benchmarks/manifest.example.json to
benchmark-manifest.local.json and replace placeholders with authorized sources.
Use a read/process/edit role token:

CLIPFAC_AGENT_TOKEN=... python tools/clipfac_benchmark.py \
  --manifest benchmark-manifest.local.json --max-clips 3 \
  --output benchmark-results.json

The runner handles at most 30 sources and 1–5 clips per source, checkpoints each
case, measures elapsed time and sampled-review outcomes, and never publishes.
It uses your configured provider resources. Human quality ratings and unknown
cost remain null; they are not fabricated from model confidence. Rate output
separately for hook strength, context, pacing, framing, caption legibility and
brand fit before declaring the product production-ready.
