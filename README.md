# Clipfac

A self-hosted video workspace for creators and AI agents, built on OpenShorts.

Bring source footage and a campaign brief. Choose a versioned editing style.
Generate clips, inspect the result and publish through the existing editor or MCP.

- Six rendering presets plus automatic direction from sampled frames and transcript.
- Analyze first, review a source contact sheet, then render immutable edit plans.
- Campaign-aware selection with an explicit shortlist and hold reasons.
- Persistent, idempotent production submissions.
- Scoped installation tokens and a same-origin dashboard.
- MCP production, transcript, scene and technical inspection tools.
- Warm neutral UI, blue accents, responsive layout and light/dark appearances.

## Deploy

~~~bash
cp .env.clipfac.example .env
# Configure CLIPFAC_ADMIN_TOKEN and provider credentials.
docker compose -f compose.clipfac.yml up --build -d
~~~

Open http://localhost:8080. The production profile fails closed without an
installation token. It persists output, uploads, model cache and production data.
Only a loopback frontend port is exposed; use HTTPS proxying for remote agents.

[Setup, agent scopes, API and limitations](docs/clipfac.md)

## Current boundaries

This layer extends upstream clipping and recovery with staged analysis and immutable
plans. Autonomous editorial repair, low-resolution motion previews and more than
15 candidates per source remain future work. Presets are implemented
configurations, not benchmarked quality claims. Technical inspection cannot
certify creative quality. Views are never guaranteed.

## Development

~~~bash
pip install -r requirements.txt
pytest tests/ -v
cd dashboard
npm ci
npm run build
npm run lint
~~~

The original development compose remains available. Production uses
compose.clipfac.yml with a static frontend and authenticated same-origin API.

## Attribution and license

Built on [OpenShorts](https://github.com/mutonby/openshorts) by its contributors,
derived from [Autocrop-vertical](https://github.com/kamilstanuch/Autocrop-vertical).
Original copyright notices are retained. Core and the new clipfac/ layer use
[MIT](LICENSE). The upstream [cloud/ directory](cloud/LICENSE) retains its
separate commercial license and is not needed for standalone operation.
