# Clipfac

A universal, self-hosted video production workspace for creators and AI agents.
Built on OpenShorts, with a separate MIT production layer.

**Experimental branch: implementation is awaiting executable CI, Docker and
real-media validation. Keep the PR draft until those checks pass.**

## The workflow

Analyze footage -> review direction and a contact sheet -> create an immutable
edit plan -> render -> inspect and review -> repair within a budget -> publish.

- Multimodal direction from sampled frames and transcript, with six versioned looks.
- Up to 60 candidate moments per source; bounded detail batches for longer videos.
- Exact millisecond edit plans, source fingerprints and immutable revisions.
- Retained-source rendering without a second download or selection pass.
- Technical checks plus sampled visual/editorial review, tied to rendered bytes.
- Bounded repair plans that preserve the original output.
- Review-gated, idempotent publishing through connected social profiles.
- Persistent webhook outbox, retries, delivery IDs and dead-letter replay.
- Scoped agent roles sharing one installation workspace.
- Centralized provider settings with encrypted local keys and masked responses.
- Responsive neutral UI with blue accents and light/dark appearances.
- 32 MCP tools, alongside the existing REST API and detailed editor.

## Deploy

~~~bash
cp .env.clipfac.example .env
# Set CLIPFAC_ADMIN_TOKEN to a long randomly generated token.
docker compose -f compose.clipfac.yml up --build -d
~~~

Open http://localhost:8080, connect the installation token, then configure
providers in the workspace. Gemini is supported natively; text and vision can
use separate compatible endpoints. Social/media providers have their own settings.

The production profile requires authentication and passing current-render review
before clip publishing. It exposes only a loopback frontend port. Use HTTPS
reverse proxying for remote agents.

[Setup, architecture, agent scopes and limitations](docs/clipfac.md)

## Predictability and limits

Plans pin source identity, cuts and style settings. Encoded pixels are not
guaranteed identical across hardware or dependency versions. Eight sampled
frames cannot certify every frame, continuous pacing or audio aesthetics.
Confidence and editorial scores are model judgments, not measured probabilities
or guaranteed views.

Repair automation supports bounded trims, preset changes and verbatim source-quote
hooks, with at most two plans per source/campaign/candidate. More complex framing,
branding, sound design and montage fixes still require the editor or further work.
Contact sheets are storyboard previews, not low-resolution motion renders.
Whop/Tryshoot campaign briefs must be supplied by the user or researching agent.

## Development

~~~bash
pip install -r requirements.txt
pytest tests/ -v
cd dashboard
npm ci
npm run build
npm run lint
~~~

CI includes backend tests and frontend build/lint. Additional regression suites
cover staged plans, credentials, cross-role handoffs, reviews, repair limits,
publishing idempotency and webhook leases. These are implemented tests, not
a claim that they have run in this environment.

## Attribution and license

Built on [OpenShorts](https://github.com/mutonby/openshorts) by its contributors,
derived from [Autocrop-vertical](https://github.com/kamilstanuch/Autocrop-vertical).
Original copyright notices are retained. Core and clipfac/ use [MIT](LICENSE).
The upstream [cloud/ directory](cloud/LICENSE) retains its separate commercial
license and is not required for standalone Clipfac.
