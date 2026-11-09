# Clipfac dashboard UI audit — one consistent UI

Scope: `dashboard/src` on branch `feat/clipfac-production` (HEAD `7d74e96`).
Method: read `App.jsx` (2615 lines), `ClipfacWorkspace.jsx` (374), all components,
`AuthContext.jsx`, `lib/api.js`, `config.js`, `main.jsx`; verified backend
endpoints in `app.py` / `clipfac/vault.py` before calling anything dead.
Rule for this pass: **the server-side provider vault is the single home for
every provider key** (`clipfac/vault.py:18-29` maps gemini/text/vision/
upload_post/elevenlabs/fal/youtube to env vars applied to the backend
process). Browser-local keys are duplicates.

## 1. Current state: two apps behind one URL

`main.jsx:10-18` — hash `#app*` renders the Editor (`App.jsx`); anything else
renders the Workspace (`ClipfacWorkspace.jsx`). They are two separate shells
with two navs, two sidebars, two themes of the same tokens, and cross-links
instead of integration (`ClipfacWorkspace.jsx:225,230`, `App.jsx:1376`).

| | Workspace (default route) | Editor (`#app`) |
|---|---|---|
| Nav | Productions, Style library, Agent connections, Provider connections (`ClipfacWorkspace.jsx:220`) | Clip Generator, Autopilot*, AI Shorts, AI Agent, UGC Gallery, YouTube Studio, History*, Settings (`App.jsx:1304-1314`; *billing-gated, dead on self-host) |
| Login | Install-token form (`ClipfacWorkspace.jsx:206-213`) | Cloud magic-link/Google modal (`LoginModal`, dead on self-host) |
| Projects | `GET /api/clipfac/productions` (`:41`) | `HistoryTab` → `GET /api/history` + `GET /api/projects` — **both endpoints do not exist in `app.py`** (only `POST /api/projects/:id/restore` at `app.py:4499`) |
| Processing | Selected-production 5s poll (`:56-76`), log tail (`:272`) | 2s job poll (`App.jsx:819-870`), live logs, clips appearing (`:2395-2430`) |
| Clip editing | Plan editor (ms-accurate cuts, `:303-315`), inspect/review (`:290-301`) | `ResultCard` grid + `ClipEditor` (EDL) + `ReframeEditor` (crop) (`App.jsx:2551-2568`) |
| Publish | MCP-only, no UI | `ResultCard` / `ScheduleWeekModal` / `SaaShortsTab` publish modals + `ThumbnailStudio` YouTube publish |
| Settings | Provider cards, vault-backed (`:318,327-374`) | Gemini/ElevenLabs/fal key inputs, `McpConnectCard`, social links (`App.jsx:1658-1881`) |

## 2. Duplicates — what merges into what

| # | Duplicate | Evidence | Merge into |
|---|---|---|---|
| D1 | Two shells / two navs | `main.jsx:21`; `ClipfacWorkspace.jsx:215-228` vs `App.jsx:1380-1530` | **One `Shell`** (workspace `cf-*` layout). One nav: Projects, Clips, AI Shorts, Thumbnails, Gallery, Settings |
| D2 | Two logins, one token key | Workspace token form `ClipfacWorkspace.jsx:206-213`; `LoginModal` via `AuthContext` (`App.jsx:2569`); both write `openshorts_auth` (`lib/api.js:9`) | Workspace unlock only. Delete `LoginModal` + cloud auth flows (dead: `AuthContext.jsx:145-148` never runs them when billing is off) |
| D3 | Gemini key in two homes | Vault card `ClipfacWorkspace.jsx:328-336,344-352` vs browser `KeyInput` `App.jsx:1717` (+ `localStorage gemini_key`, `App.jsx:299,746`) vs key modal input `App.jsx:2499-2511` | Vault only. Server resolves Gemini from `GEMINI_API_KEY` (`app.py:172-188`); browser key is an override nobody needs once the vault has it |
| D4 | ElevenLabs / fal keys in two homes | Vault cards (`clipfac/vault.py:23-24`) vs App settings inputs `App.jsx:1783-1787,1841-1845` | Vault only. Server maps `ELEVENLABS_API_KEY` / `FAL_KEY` (`clipfac/vault.py:235`) |
| D5 | Upload-Post key: vestigial browser state | `App.jsx:301-306` holds `uploadPostKey`; `setUploadPostKey` is **never called**; UI copy already says the vault is canonical (`App.jsx:1756`) | Vault only; delete the state + prop threading |
| D6 | MCP connection in two places | Workspace Agents tab (`ClipfacWorkspace.jsx:319`, `:204`) vs `McpConnectCard` in App settings (`App.jsx:1671`) | One Agents section inside Settings |
| D7 | Three galleries, no canonical one | `Gallery.jsx` (`GET /api/gallery/clips` — route commented out, `app.py:7791`) vs `HistoryTab` (endpoints don't exist, §1) vs `UGCGallery` (`/api/saasshorts/gallery`, live) | Keep `UGCGallery` only; delete `Gallery`/`GalleryCard` + `HistoryTab` |
| D8 | Publish UI 3× (same endpoint) | `ResultCard.jsx:738`, `ScheduleWeekModal.jsx:151`, `SaaShortsTab.jsx:1459` all `POST /api/social/post`; `ThumbnailStudio.jsx:407` separate YouTube publish | **Kept as-is this pass** (single vs bulk vs AI-short UX differ; unifying is a component rewrite). Flagged for a follow-up, not deleted |
| D9 | Transcript fetch 2× | `ResultCard.jsx:277` + `SubtitleModal.jsx:182`, same `GET /api/clip/{job}/{index}/transcript` | **Kept as-is this pass** (different purposes: duration vs captions). Flagged |
| D10 | "AI Agent" tab is a static ad | `App.jsx:1888-2002` — no component mounted, just GitHub CTA for an external skill | Delete from nav (not a tool, not a duplicate — a dead screen) |
| D11 | Theme toggle + "Editor settings" link | Workspace sidebar bottom (`ClipfacWorkspace.jsx:225-226`) | Theme moves into Settings; link deleted (Settings is one nav item) |
| D12 | Style library as a nav item | `ClipfacWorkspace.jsx:220,317` — a picker, reachable from the production form (`:245`) | Remove from nav; keep reachable from the form |
| D13 | Download/copy 4× | `ResultCard.jsx:129`, `GalleryCard.jsx:50`, `HistoryTab.jsx:103`, `UGCGallery.jsx:133-140` | Shrinks to 2 when Gallery/History go (ResultCard + UGCGallery). No further work |
| D14 | Gemini-key warnings that lie | `ThumbnailStudio.jsx:86,504`, `SaaShortsTab.jsx:47` warn when no *browser* key, ignoring the vault | Point them at vault state: App fetches `GET /api/clipfac/providers` once and passes `token_configured` down; warnings show only when the vault lacks the key |
| D15 | Cost/minutes readouts 2× | `SourceCost.jsx` (managed-only, returns null on self-host) vs `ClipEditor.jsx:1113` rerender minutes | Delete `SourceCost` usage from `MediaInput.jsx` (renders null here); keep ClipEditor's |
| D16 | Quota/upsell plumbing in the editor | `App.jsx:459-493,941-949,1051-1065,1112-1250,2131-2155,2279-2343,2581-2611` (top-up, trial, watermark, plan choice, nudges, partial-job) | Delete all — 402/quota never happens with billing off |

## 3. Dead code — cloud/billing, deletable outright

Verified dead on self-host (`BILLING_ENABLED=0` → `billingEnabled=false`,
`isManaged=false`, `isSignedIn=false`, `plan=null`):

- Components: `PricingPage`, `PricingSection`, `PlanChoiceModal`, `TopUpModal`,
  `TrialGate`, `TrialUpgradeModal`, `CancelPlanModal`, `InvoicesCard`,
  `DeleteAccountCard`, `AccountPage`, `UsageMeter`, `WatermarkModal`,
  `LoginModal`, `OnboardingSurvey`, `ProfileMenu`, `ApiKeysCard`,
  `SocialAnalyticsCard`, `Landing`, `Legal`, `SiteNav`, `CookieBanner`
  (unmounted), `OAuthConsent` (unmounted; no route ever mounted it),
  `Gallery`, `GalleryCard`, `HistoryTab`, `AutopilotTab`, `KeyInput`
  (only used by the deleted App settings Gemini input), `McpConnectCard`
  (only used by deleted settings + dead `AccountPage`), `SourceCost`
  (managed-only null on self-host).
- Lib: `lib/pendingPlan.js` (only `PricingSection` + one `AuthContext` line),
  `lib/backFromStripe.js` (only dead billing components).
- CSS: `App.css` (untouched Vite boilerplate, nothing imports it),
  `landing.css` (only `Landing.jsx:6` imports it).
- `AuthContext.jsx`: keep the file (token/config provider) but remove the
  pending-plan redirect (`:121` + import `:12`); its magic-link/Google/
  password flows never run when billing is off (`:145-148`).
- `lib/mediaToken.js`: dead on self-host (only reachable via `refreshMe`,
  which never runs); keep the file, it is inert — deleting it would require
  touching `AuthContext` imports for no runtime gain. **Left in place.**
- `StarBanner.jsx`: LIVE (renders during processing + in SaaShortsTab) but
  points at the wrong repo (`mutonby/openshorts`, `:3`). Fix URL to
  `abdlx/Clipfac`, keep the component.
- `App.jsx` internals to delete: `durableClips` state + `fetchDurableMap` +
  both R2 effects (`:440-448,784-802` — endpoint doesn't exist, gated by
  `isManaged` anyway); `uploadPostKey` state; `apiKey`/`elevenLabsKey`/
  `falKey` state + persist effects; `showTopUp/showWmNotice/showPlanChoice/
  showTrialUpgrade/tutorialPhase/topUpInfo/partialJob/firstVideoJob/
  autopilotOn` state; `openUpsell`, quota-wall listener, `stashPendingJob`
  + resume effect (cloud sign-in flow); `restoreProject` (only reachable
  from dead `HistoryTab`); settings tab (D3–D6); autopilot/history/ai-agent
  tab renders; `TrialGate`/`AdvancedBanner` renders; header billing blocks
  (`UsageMeter`, plan button, Sign in, `ProfileMenu`).

## 4. Design — one shell

**Shell** (`src/components/Shell.jsx`, new): owns unlock passthrough, theme
(`clipfac_theme`), and the single nav. Uses the existing `cf-*` layout
(`clipfac.css`) and tokens — no rebrand. Mobile: the existing `cf-sidebar`
collapses to an icon top row at ≤720px; content stacks single-column;
inputs are 16px (no iOS zoom); buttons ≥38px targets.

| Nav | View | Source (existing code, moved not rewritten) |
|---|---|---|
| Projects | All productions + live status | `ClipfacWorkspace` productions tab: analyse form, productions list, selected-production detail (analysis → plan → render, inspect/review) |
| Clips | Current job: live processing + clips appearing + edit/publish | `App.jsx` dashboard tab: `MediaInput`, poller, `ResultCard` grid, `ClipEditor`, `ReframeEditor` |
| AI Shorts | AI Shorts generator | `SaaShortsTab` |
| Thumbnails | Thumbnail studio | `ThumbnailStudio` |
| Gallery | AI Shorts gallery | `UGCGallery` |
| Settings | **One settings view**: Providers (all 7 vault cards) + Agents (MCP) + Editor preferences + About | `ProviderCard` (extracted), `AgentConnections` (extracted), prefs writing the same `localStorage` keys `MediaInput` already reads |

**Graceful states per view:**

| View | Loading | Empty | Error | Processing | Done |
|---|---|---|---|---|---|
| Projects | Skeleton rows on first `refresh()` | "Your next production starts here" (existing `cf-empty`) | Dismissible `cf-error` banner (existing) | Status badge on the row + auto-refresh while any production is non-terminal; selected production polls every 5s (existing) | State badge `completed` + clips grid |
| Clips | `ProcessingAnimation` + log tail (existing) | Idle hero + `MediaInput` (existing) | "Generation failed" + readable server error (existing `readableError`) | 2s poll; clips appear in the grid as they land; per-clip progress | `ResultCard` grid, best-first (existing `rankedClips`) |
| AI Shorts / Thumbnails / Gallery | Existing step spinners / skeletons | Existing empty states | Existing inline errors | Existing step progress | Existing result/publish states |
| Settings | Providers load with the workspace `refresh()` | "Not configured" per card (existing `cf-status`) | Same dismissible banner | Save spinner per card (existing) | "Configured" badge per card |
| Unlock (first visit) | "Opening Clipfac…" (existing `Suspense` fallback) | — | Wrong-token error under the form (existing) | — | Workspace opens |

**Routing:** `main.jsx` renders `<Shell>` always. Hash compat kept:
`#app` → Clips, `#app?tab=saasshorts|thumbnails|ugc-gallery` → that view,
`#app?tab=settings` → Settings, anything else → Projects. `#workspace` →
Projects. Deep-link tabs that no longer exist (`autopilot`, `history`,
`ai-agent`) fall back to Projects.

**What stays untouched:** the clip pipeline, the review gate, all API
endpoints and auth handling (`openshorts_auth` Bearer), `ResultCard` /
`ClipEditor` / `ReframeEditor` / `SaaShortsTab` / `ThumbnailStudio` /
`UGCGallery` internals, `SubtitleModal` / `HookModal` / `TranslateModal`,
`ScheduleWeekModal`, `MediaInput` (minus `SourceCost`), `ProcessingAnimation`,
`ui/` primitives (`Modal`, `SegmentedControl`, `StepIndicator`).

## 5. Open questions / needs the user's eyes

1. **ElevenLabs/fal keys must move to the vault.** After this change the only
   place to save them is Settings → Provider connections. If he has them only
   in the old editor settings (browser), he must re-save them once in the
   vault. Same for the Gemini browser key.
2. **History is gone, not merged.** The legacy `/api/history` + `/api/projects`
   endpoints don't exist in this backend, so past `/api/process` jobs have no
   list view anywhere. Restoring an old job by ID (`POST /api/projects/:id/
   restore`) still exists server-side but has no UI. If he wants old jobs
   listed, that's backend work, not UI work.
3. **Publish UI stays 3×** (D8) and **transcript fetch stays 2×** (D9) — both
   flagged, deliberately not unified in this pass (component rewrites with
   real breakage risk).
4. **`mediaToken.js` and `AuthContext` cloud flows left in place** (inert, not
   UI). Deleting them is a separate cleanup.
5. **AI Agent tab deleted** — it was a static ad for an external skill, not a
   tool. If he uses that skill, the GitHub link is gone from the UI.

## 6. Implementation (done 2026-11-07, local only — NOT pushed, NOT deployed)

Build passes (`npm run build`, vite). App chunk 1,155.80 kB → 1,041.02 kB.
`App.jsx` 2,615 → ~1,080 lines. 34 files deleted, 13 modified, 3 new.

### What changed vs the §4 design
- **Theme toggle stayed in the sidebar** (§4 said it moves into Settings).
  The sidebar bottom is the single place for it; Settings has no theme row.
- **`WatermarkModal.jsx` KEPT, not deleted.** `ResultCard` still renders it
  on download. It is now gated to managed mode only (`isManaged && plan ===
  'free'` in `ResultCard.jsx`): on self-host the download goes straight
  through — before this fix the self-host `/api/me` stub reports
  `plan: 'free'`, so the bogus "free plan watermark / deleted after 7 days"
  upsell showed before every job's first download, with an upgrade button
  that navigated to the dead `#/pricing` hash.
- **`SourceCost` removed from `MediaInput`** (import + JSX). It returns
  `null` on self-host (`if (!isManaged) return null`), so the range picker
  never rendered here anyway; the `range` state + `sourceStart`/`sourceEnd`
  payload lines stay (always null on self-host, same as before).
- **`AuthContext.jsx`: `readPendingPlan` import + pricing-redirect line
  removed** (the pending-plan file is deleted; the line was cloud-only).
- **`lib/api.js`: `apiFetch` no longer reads `localStorage gemini_key`.**
  A stale browser key used to be sent as `X-Gemini-Key` on every call and
  would have overridden the vault key server-side (the provider middleware
  only injects when the header is absent). Vault-only now.
- **Editor mounts only after providers load** (`providersLoaded` gate in
  `EditorView`): without it the editor would flash the "keys missing"
  banner on every load while the vault state was still in flight. A
  providers-fetch failure shows the workspace error instead of spinning.

### Files changed
- New: `src/components/ProviderCard.jsx`, `src/components/AgentConnections.jsx`,
  `src/components/SettingsView.jsx` (extracted verbatim from the workspace;
  `ProviderCard`/`AgentConnections` keep their original copy and behavior).
- Modified: `src/components/ClipfacWorkspace.jsx` (the single shell),
  `src/main.jsx` (always renders the shell; hash → view mapping),
  `src/App.jsx` (embedded mode, billing/tutorial/history/autopilot dead code
  removed, key states removed, `vaultKeys` plumbing),
  `src/components/ResultCard.jsx` (vault key checks, watermark gate),
  `src/components/SaaShortsTab.jsx` (vault key checks, headers removed),
  `src/components/ThumbnailStudio.jsx` (vault key checks, headers removed),
  `src/components/ScheduleWeekModal.jsx` (dead key prop removed),
  `src/components/MediaInput.jsx` (SourceCost removed),
  `src/components/StarBanner.jsx` (repo URL → `abdlx/Clipfac`),
  `src/contexts/AuthContext.jsx` (pendingPlan removal),
  `src/lib/api.js` (browser Gemini key read removed),
  `src/clipfac.css` (`.cf-editor-wrap` + small-phone nav fit).
- Deleted (34): `PricingPage`, `PricingSection`, `PlanChoiceModal`,
  `TopUpModal`, `TrialGate`, `TrialUpgradeModal`, `CancelPlanModal`,
  `InvoicesCard`, `DeleteAccountCard`, `AccountPage`, `UsageMeter`,
  `LoginModal`, `OnboardingSurvey`, `ProfileMenu`, `ApiKeysCard`,
  `SocialAnalyticsCard`, `Landing`, `Legal`, `SiteNav`, `CookieBanner`,
  `OAuthConsent`, `Gallery`, `GalleryCard`, `HistoryTab`, `AutopilotTab`,
  `KeyInput`, `McpConnectCard`, `SourceCost`, `ClipTutorial`,
  `AdvancedBanner`, `lib/pendingPlan.js`, `lib/backFromStripe.js`,
  `App.css`, `landing.css`. (All git-tracked — recoverable via git.)

### Left as-is (known, flagged)
- `QuotaError` / `openQuotaWall` / `QUOTA_WALL_EVENT` still exist in
  `lib/api.js` and in `ClipEditor`/`ThumbnailStudio` catch blocks. They are
  inert on self-host (no 402s when billing is off) and the catches return
  safely with no listener; removing them means touching those components'
  error paths — a separate pass.
- `AuthContext` cloud flows (magic-link/Google redirect, plan state) left in
  place — inert when billing is off, and `App` still reads `isManaged` /
  `jobRetentionSeconds` / `localLlm` from it.
- `lib/mediaToken.js` left in place (inert, not UI).
- Publish UI stays 3×, transcript fetch stays 2× (see §5.3).
- `durable` / `initialState` / `onStateChange` props still exist on
  `ResultCard` (optional, all `?.`-guarded) — the durable fallback simply
  never fires on self-host now that nothing passes them.
- The workspace's `isManaged` prop is still passed to `SaaShortsTab` /
  `ThumbnailStudio` / `ResultCard` (always false on self-host); their
  managed branches are dead but harmless.
