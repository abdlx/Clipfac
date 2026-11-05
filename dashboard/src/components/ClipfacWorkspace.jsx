import { useCallback, useEffect, useState } from 'react'
import { ArrowUpRight, Check, ChevronRight, Clapperboard, Copy, Film, KeyRound, Loader2, Moon, Plus, RefreshCw, Settings2, ShieldCheck, SlidersHorizontal, Sparkles, Sun, Workflow } from 'lucide-react'
import { apiJson } from '../lib/api'
import { getApiUrl } from '../config'

const initial = { source_url: '', style: 'auto', campaign_name: '', platform: 'independent', audience: '', objective: '', notes: '', required: '', forbidden: '', minimum_score: 70, min_seconds: 15, max_seconds: 60, max_clips: 15, confirm_rights: false, captions: true }
const lines = value => value.split('\n').map(x => x.trim()).filter(Boolean)
const errorText = error => error.detail || error.message || 'Request failed'
const requestKey = () => crypto.randomUUID?.() || Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, '0')).join('')

export default function ClipfacWorkspace() {
  const [tab, setTab] = useState('productions')
  const [presets, setPresets] = useState([])
  const [providers, setProviders] = useState([])
  const [form, setForm] = useState(initial)
  const [productions, setProductions] = useState([])
  const [selected, setSelected] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [locked, setLocked] = useState(false)
  const [token, setToken] = useState('')
  const [theme, setTheme] = useState(() => { try { return localStorage.getItem('clipfac_theme') || 'light' } catch (_) { return 'light' } })
  const [report, setReport] = useState(null)
  const [inspecting, setInspecting] = useState(null)
  const [copied, setCopied] = useState(false)
  const [submission, setSubmission] = useState(null)
  const [editPlan, setEditPlan] = useState(null)
  const [planDirty, setPlanDirty] = useState(false)
  const [renderSubmission, setRenderSubmission] = useState(null)
  const [review, setReview] = useState(null)
  const [reviewing, setReviewing] = useState(null)

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    try { localStorage.setItem('clipfac_theme', theme) } catch (_) { /* theme still works for this session */ }
  }, [theme])

  const refresh = useCallback(async () => {
    try {
      const [styles, history, credentials] = await Promise.all([
        apiJson('/api/clipfac/presets'), apiJson('/api/clipfac/productions'), apiJson('/api/clipfac/providers'),
      ])
      setPresets(styles.presets)
      setProviders(credentials.providers)
      setProductions(history.productions)
      setLocked(false)
    } catch (err) {
      if (err.status === 401) setLocked(true)
      else setError(errorText(err))
    }
  }, [])

  useEffect(() => { refresh() }, [refresh])

  const selectedId = selected?.id
  const running = selected && !['completed', 'failed', 'blocked', 'uncertain', 'unavailable', 'submitted'].includes(selected.state)
  useEffect(() => {
    if (!selectedId || !running) return
    let cancelled = false
    let timer
    async function poll() {
      try {
        const data = await apiJson('/api/clipfac/productions/' + selectedId)
        if (!cancelled) {
          setSelected(data)
          timer = setTimeout(poll, 5000)
        }
      } catch (err) {
        if (!cancelled) {
          setError(errorText(err))
          timer = setTimeout(poll, 15000)
        }
      }
    }
    poll()
    return () => { cancelled = true; clearTimeout(timer) }
  }, [selectedId, running])

  const update = (key, value) => { setForm(old => ({ ...old, [key]: value })); setSubmission(null) }
  async function saveProvider(kind, body) {
    setError('')
    try {
      await apiJson('/api/clipfac/providers/' + kind, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      await refresh()
    } catch (err) { setError(errorText(err)); throw err }
  }
  async function removeProvider(kind) {
    setError('')
    try {
      await apiJson('/api/clipfac/providers/' + kind, { method: 'DELETE' })
      await refresh()
    } catch (err) { setError(errorText(err)); throw err }
  }
  async function unlock(event) {
    event.preventDefault()
    setBusy(true); setError('')
    try {
      await apiJson('/api/clipfac/session', { method: 'POST', headers: { Authorization: 'Bearer ' + token } })
      setToken('')
      await refresh()
    } catch (err) { setError(errorText(err)) }
    finally { setBusy(false) }
  }
  function payload() {
    return {
      source_url: form.source_url.trim(), confirm_rights: form.confirm_rights, style: form.style,
      campaign: { name: form.campaign_name, platform: form.platform, audience: form.audience, objective: form.objective,
        notes: form.notes, required_phrases: lines(form.required), forbidden_phrases: lines(form.forbidden) },
      minimum_score: Number(form.minimum_score), min_seconds: Number(form.min_seconds),
      max_seconds: Number(form.max_seconds), max_clips: Number(form.max_clips), captions: form.captions,
    }
  }
  async function produce(event) {
    event.preventDefault()
    setBusy(true); setError(''); setReport(null); setReview(null); setEditPlan(null); setPlanDirty(false)
    // Hold this exact key + body across ambiguous failures. Editing the form starts a new request.
    const body = submission || { ...payload(), idempotency_key: requestKey() }
    setSubmission(body)
    try {
      const data = await apiJson('/api/clipfac/analyses', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      setSelected(data)
      await refresh()
    } catch (err) { setError(errorText(err)) }
    finally { setBusy(false) }
  }
  async function openProduction(id) {
    setBusy(true); setError(''); setReport(null); setReview(null); setEditPlan(null)
    try { setSelected(await apiJson('/api/clipfac/productions/' + id)) }
    catch (err) { setError(errorText(err)) }
    finally { setBusy(false) }
  }
  async function createPlan() {
    setBusy(true); setError('')
    try {
      const args = selected.request.arguments || selected.request
      const body = { analysis_job_id: selected.response.job_id, minimum_score: args.minimum_score ?? 70, captions: args.captions ?? true }
      if (args.style !== 'auto') body.style = args.style
      const plan = await apiJson('/api/clipfac/plans', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      setEditPlan(plan); setPlanDirty(false); setRenderSubmission(null)
    } catch (err) { setError(errorText(err)) }
    finally { setBusy(false) }
  }
  function changeCut(index, key, value) {
    setEditPlan(old => ({ ...old, clips: old.clips.map((clip, position) => position === index ? { ...clip, [key]: value } : clip) }))
    setPlanDirty(true); setRenderSubmission(null)
  }
  async function savePlan() {
    setBusy(true); setError('')
    try {
      const plan = await apiJson('/api/clipfac/plans/revise', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plan_id: editPlan.id, expected_revision: editPlan.revision, clips: editPlan.clips, style: editPlan.style, captions: editPlan.captions }) })
      setEditPlan(plan); setPlanDirty(false); setRenderSubmission(null)
    } catch (err) { setError(errorText(err)) }
    finally { setBusy(false) }
  }
  async function renderPlan() {
    if (planDirty) { setError('Save the edit-plan revision before rendering.'); return }
    setBusy(true); setError('')
    const body = renderSubmission || { plan_id: editPlan.id, expected_revision: editPlan.revision, idempotency_key: requestKey(), quality: 'final' }
    setRenderSubmission(body)
    try {
      const production = await apiJson('/api/clipfac/plans/render', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      setSelected(production); setReview(null); await refresh()
    } catch (err) { setError(errorText(err)) }
    finally { setBusy(false) }
  }
  async function reviewClip(index, retryFailed = false) {
    setReviewing(index); setError('')
    try {
      const result = await apiJson('/api/clipfac/reviews', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_id: selected.response.job_id, clip_index: index, retry_failed: retryFailed }) })
      setReview(result)
    } catch (err) { setError(errorText(err)) }
    finally { setReviewing(null) }
  }
  async function makeRepairPlan() {
    setBusy(true); setError('')
    try {
      const result = await apiJson('/api/clipfac/reviews/repair', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ review_id: review.id }) })
      if (!result.plan_id) throw new Error('Repair planning was interrupted. Read the existing repair record before retrying.')
      const plan = await apiJson('/api/clipfac/plans/' + result.plan_id)
      setEditPlan(plan); setPlanDirty(false); setRenderSubmission(null)
    } catch (err) { setError(errorText(err)) }
    finally { setBusy(false) }
  }
  async function inspect(index) {
    setInspecting(index); setError('')
    try {
      const data = await apiJson('/api/clipfac/inspect', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_id: selected.response.job_id, clip_index: index }) })
      setReport({ index, ...data })
    } catch (err) { setError(errorText(err)) }
    finally { setInspecting(null) }
  }
  const preset = presets.find(item => item.id === form.style)
  const config = JSON.stringify({ mcpServers: { clipfac: { url: window.location.origin + '/mcp', headers: { Authorization: 'Bearer YOUR_AGENT_TOKEN' } } } }, null, 2)

  if (locked) return <div className="cf-lock"><form className="cf-panel cf-unlock" onSubmit={unlock}>
    <img src="/clipfac.svg" alt="" width="48" height="48" />
    <h1>Welcome to Clipfac.</h1><p>Connect your installation to open the workspace.</p>
    <label>Installation token<input type="password" autoComplete="off" required value={token} onChange={e => setToken(e.target.value)} placeholder="Paste your installation token" /></label>
    {error && <p className="cf-error" role="alert">{error}</p>}
    <button className="cf-primary" disabled={busy}>{busy ? <Loader2 className="cf-spin" size={16} /> : <KeyRound size={16} />} Connect workspace</button>
    <small>The token is exchanged for an HttpOnly session and is not stored in browser storage.</small>
  </form></div>

  return <div className="cf-shell">
    <aside className="cf-sidebar">
      <a className="cf-brand" href="#workspace"><img src="/clipfac.svg" alt="" /><span>Clipfac<span className="cf-brand-dot">.</span></span></a>
      <div className="cf-workspace-label">YOUR WORKSPACE</div>
      <nav aria-label="Workspace">
        {[['productions', Film, 'Productions'], ['styles', SlidersHorizontal, 'Style library'], ['agents', Workflow, 'Agent connections'], ['providers', KeyRound, 'Provider connections']].map(([id, Icon, label]) =>
          <button key={id} aria-current={tab === id ? 'page' : undefined} className={tab === id ? 'cf-nav active' : 'cf-nav'} onClick={() => { setTab(id); setError('') }}><Icon size={18} />{label}{tab === id && <span className="cf-nav-dot" />}</button>)}
      </nav>
      <div className="cf-sidebar-bottom">
        <div className="cf-local"><span /> Self-hosted workspace<small>Your footage. Your providers.</small></div>
        <a className="cf-nav" href="#app?tab=settings"><Settings2 size={18} />Editor settings<ArrowUpRight size={14} /></a>
        <button className="cf-nav" onClick={() => setTheme(theme === 'light' ? 'dark' : 'light')}>{theme === 'light' ? <Moon size={18} /> : <Sun size={18} />}{theme === 'light' ? 'Dark appearance' : 'Light appearance'}</button>
      </div>
    </aside>
    <main className="cf-main">
      <header className="cf-topbar"><span>Workspace <ChevronRight size={14} /> <strong>{tab === 'productions' ? 'Productions' : tab === 'styles' ? 'Style library' : tab === 'providers' ? 'Provider connections' : 'Agent connections'}</strong></span><a href="#app">Open editor <ArrowUpRight size={14} /></a></header>
      <div className="cf-content">
        {error && <div className="cf-error" role="alert">{error}<button onClick={() => setError('')} aria-label="Dismiss error">×</button></div>}
        {tab === 'productions' && <>
          <div className="cf-heading"><div><p className="cf-kicker">FROM LONG FORM TO YOUR NEXT STORY</p><h1>A little footage.<br /><span>A lot of possibility.</span></h1><p>Give every worthwhile moment its own frame.</p></div><button className="cf-secondary" onClick={() => { setSelected(null); setReport(null); setReview(null); setEditPlan(null); setSubmission(null); setRenderSubmission(null); setPlanDirty(false) }}><Plus size={16} />New production</button></div>
          <div className="cf-grid">
            <form className="cf-panel cf-production-form" onSubmit={produce}>
              <div className="cf-panel-heading"><span className="cf-icon"><Clapperboard size={19} /></span><div><h2>Start with the footage</h2><p>Analyze once. Refine the plan. Render when ready.</p></div></div>
              <label>Video link<input type="url" required value={form.source_url} onChange={e => update('source_url', e.target.value)} placeholder="Paste a YouTube or direct video link" /></label>
              <div className="cf-label-row"><span>Editing style</span><button type="button" className="cf-text-button" onClick={() => setTab('styles')}>Explore styles <ArrowUpRight size={12} /></button></div>
              <div className="cf-style-options"><button type="button" aria-pressed={form.style === 'auto'} className={'cf-style-chip ' + (form.style === 'auto' ? 'selected' : '')} onClick={() => update('style', 'auto')}><Sparkles size={12} />Auto direction{form.style === 'auto' && <Check size={12} />}</button>{presets.map(item => <button type="button" key={item.id} aria-pressed={form.style === item.id} className={'cf-style-chip ' + (form.style === item.id ? 'selected' : '')} onClick={() => update('style', item.id)}><i style={{ background: item.accent }} />{item.name}{form.style === item.id && <Check size={12} />}</button>)}</div>
              <p className="cf-hint">{form.style === 'auto' ? 'Inspect sampled frames and transcript to choose a style. Explicit fallback warnings appear if the director is unavailable.' : preset?.description || 'Loading style settings…'}</p>
              <details className="cf-details"><summary>Campaign brief <span>Optional</span></summary>
                <div className="cf-field-grid"><label>Campaign name<input value={form.campaign_name} onChange={e => update('campaign_name', e.target.value)} placeholder="Product launch" /></label><label>Source platform<select value={form.platform} onChange={e => update('platform', e.target.value)}><option value="independent">Independent</option><option value="whop">Whop</option><option value="tryshoot">Tryshoot</option><option value="other">Other</option></select></label></div>
                <label>Audience<input value={form.audience} onChange={e => update('audience', e.target.value)} placeholder="Who should this speak to?" /></label>
                <label>Objective<textarea value={form.objective} onChange={e => update('objective', e.target.value)} placeholder="The message or outcome this campaign needs." rows={2} /></label>
                <div className="cf-field-grid"><label>Required spoken phrases<textarea value={form.required} onChange={e => update('required', e.target.value)} placeholder="One phrase per line" rows={2} /></label><label>Forbidden spoken phrases<textarea value={form.forbidden} onChange={e => update('forbidden', e.target.value)} placeholder="One phrase per line" rows={2} /></label></div>
                <label>Editorial notes<textarea value={form.notes} onChange={e => update('notes', e.target.value)} placeholder="Brand rules, approved claims, context to preserve…" rows={2} /></label>
                <p className="cf-hint">Paste the campaign rules here. Clipfac does not fetch campaign briefs from Whop or Tryshoot.</p>
              </details>
              <details className="cf-details"><summary>Production controls <span>Quality over quantity</span></summary>
                <div className="cf-field-grid cf-three"><label>Minimum seconds<input type="number" min="5" max="175" value={form.min_seconds} onChange={e => update('min_seconds', e.target.value)} /></label><label>Maximum seconds<input type="number" min="10" max="180" value={form.max_seconds} onChange={e => update('max_seconds', e.target.value)} /></label><label>Clip ceiling<input type="number" min="1" max="60" value={form.max_clips} onChange={e => update('max_clips', e.target.value)} /></label></div>
                <label>Minimum editorial score: {form.minimum_score}/100<input type="range" min="0" max="100" value={form.minimum_score} onChange={e => update('minimum_score', e.target.value)} /></label>
                <label className="cf-check"><input type="checkbox" checked={form.captions} onChange={e => update('captions', e.target.checked)} />Burn styled captions</label>
              </details>
              <label className="cf-check"><input type="checkbox" required checked={form.confirm_rights} onChange={e => update('confirm_rights', e.target.checked)} />I own this content or have permission to process it.</label>
              <button className="cf-primary cf-wide" disabled={busy || !presets.length}>{busy ? <Loader2 className="cf-spin" size={17} /> : <Sparkles size={17} />}Analyze footage<ArrowUpRight size={16} /></button>
              <p className="cf-footnote">Analysis produces candidates and a contact sheet. Review the exact edit plan before spending time on full renders.</p>
            </form>
            <aside className="cf-right-column">
              <div className="cf-style-preview" style={{ '--preview-accent': preset?.accent || '#82B2FF' }}><div className="cf-preview-top"><span>YOUR DIRECTION</span><span>9:16</span></div><div className="cf-preview-frame"><div className="cf-preview-bars"><i /><i /><i /></div><div className="cf-preview-caption">Every moment<br />has <em>a story.</em></div><span className="cf-preview-stamp">TYPOGRAPHY CONCEPT · NOT A VIDEO PREVIEW</span></div><div className="cf-preview-bottom"><strong>{form.style === 'auto' ? 'Automatic direction' : preset?.name || 'Editorial'}</strong><small>{form.style === 'auto' ? 'Chosen after analysis' : 'Version ' + (preset?.version || 1) + ' · ' + (preset?.theme || 'podcast')}</small></div></div>
              <div className="cf-panel cf-how"><ShieldCheck size={20} /><h3>A considered workflow.</h3><p>Inspect real frames. Pin the source and cut times. Keep every revision. Render the plan you reviewed.</p><button type="button" className="cf-text-button" onClick={() => setTab('providers')}>Connect your providers <ArrowUpRight size={13} /></button></div>
            </aside>
          </div>
          <section className="cf-history"><div className="cf-section-heading"><h2>Recent productions <span>{productions.length}</span></h2><button className="cf-text-button" onClick={refresh}><RefreshCw size={14} />Refresh</button></div>
            {productions.length ? <div className="cf-production-list">{productions.map(item => <button className={'cf-history-row ' + (selected?.id === item.id ? 'selected' : '')} key={item.id} onClick={() => openProduction(item.id)}><span className="cf-history-icon"><Film size={17} /></span><span><strong>{item.request.campaign?.name || item.request.arguments?.campaign?.name || item.request.source_url || item.request.arguments?.source_url || (item.request.operation === 'render_plan' ? 'Edit plan render' : item.request.operation === 'publish' ? 'Social publication' : 'Uploaded video')}</strong><small>{item.request.style || item.request.arguments?.style || item.request.operation} · {new Date(item.created * 1000).toLocaleString()}</small></span><span className="cf-status">{selected?.id === item.id ? selected.state : item.state}</span><ChevronRight size={16} /></button>)}</div> : <div className="cf-empty"><Film size={24} /><h3>Your next production starts here.</h3><p>Add a video above, or let an agent bring one in.</p></div>}
          </section>
          {selected && <section className="cf-results"><div className="cf-section-heading"><h2>Production results</h2><span className="cf-status">{selected.state}</span></div>
            {selected.state === 'submitting' && <p className="cf-notice">Submission is in progress or was interrupted. Reusing the request will not start duplicate work. Check the instance before submitting again.</p>}
            {selected.state === 'uncertain' && <p className="cf-notice">Submission was interrupted. Check processing jobs before starting another request.</p>}
            {selected.state === 'blocked' && <pre className="cf-code">{JSON.stringify(selected.response, null, 2)}</pre>}
            {selected.state === 'failed' && <p className="cf-error">Processing failed. Review the latest job logs below.</p>}
            {selected.job?.logs?.length > 0 && <details className="cf-details"><summary>Processing log</summary><pre className="cf-code">{selected.job.logs.slice(-20).join('\n')}</pre></details>}
            {selected.analysis && <div className="cf-panel cf-analysis">
              <div className="cf-section-heading"><h2>Footage direction</h2><span className="cf-status">{selected.analysis.profile.mode || 'analyzed'}</span></div>
              {selected.analysis.contact_sheet_url && <a href={getApiUrl(selected.analysis.contact_sheet_url)} target="_blank" rel="noreferrer"><img src={getApiUrl(selected.analysis.contact_sheet_url)} alt="Sampled source frames with timestamps" className="cf-contact-sheet" /></a>}
              <h3>{selected.analysis.preset.name} · {Math.round(selected.analysis.profile.confidence * 100)}% model confidence</h3>
              <p>{selected.analysis.profile.reasoning}</p>
              {selected.analysis.warnings.map((warning, index) => <p className="cf-notice" key={index}>{warning}</p>)}
              <p className="cf-hint">{selected.analysis.candidates.length} candidate moments · No clips rendered yet. Confidence is a model judgment, not measured accuracy.</p>
              <button className="cf-primary" disabled={busy} onClick={createPlan}><SlidersHorizontal size={15} />Build edit plan</button>
            </div>}
            {selected.selection && <><p className="cf-hint">{selected.selection.accepted.length} shortlisted · {selected.selection.held.length} held for review. {selected.selection.review_notice}</p><div className="cf-clip-grid">{selected.selection.accepted.map(({ index, score, clip }) => <article className="cf-panel cf-clip" key={index}><video src={getApiUrl(clip.video_url)} controls preload="metadata" playsInline /><div><span className="cf-kicker">CLIP {index + 1} · EDITORIAL SCORE {score}</span><h3>{clip.title || clip.video_title_for_youtube_short || 'Untitled moment'}</h3><button className="cf-secondary" disabled={inspecting !== null} onClick={() => inspect(index)}>{inspecting === index ? <Loader2 size={14} className="cf-spin" /> : <ShieldCheck size={14} />}Inspect render</button><button className="cf-secondary" disabled={reviewing !== null} onClick={() => reviewClip(index)}>{reviewing === index ? <Loader2 size={14} className="cf-spin" /> : <Sparkles size={14} />}Visual review</button><a href={getApiUrl(clip.download_url || clip.video_url)} download>Download <ArrowUpRight size={13} /></a></div></article>)}</div>{selected.selection.held.length > 0 && <details className="cf-details"><summary>Held clips and reasons</summary><ul>{selected.selection.held.map(item => <li key={item.index}>Clip {item.index + 1}: {item.reasons.join(', ').replaceAll('_', ' ')}</li>)}</ul></details>}</>}
            {report && <div className="cf-panel cf-report"><h3>Clip {report.index + 1}: {report.status.replaceAll('_', ' ')}</h3><p>{report.width} × {report.height} · {report.duration_seconds?.toFixed(1)}s · Audio {report.audio ? 'present' : 'missing'}</p><p>{report.issues.length ? report.issues.join(', ').replaceAll('_', ' ') : 'No defects detected by the implemented technical checks.'}</p><small>Still requires review: {report.not_checked.join(', ').replaceAll('_', ' ')}.</small></div>}
            {review && <div className="cf-panel cf-report"><div className="cf-section-heading"><h3>Clip {review.clip_index + 1}: visual review</h3><span className="cf-status">{review.state}</span></div>
              {review.report?.contact_sheet_url && <img src={getApiUrl(review.report.contact_sheet_url)} className="cf-contact-sheet" alt="Actual sampled frames from the rendered clip" />}
              <p>{review.report?.visual?.summary || review.report?.notice || 'Review is in progress or was interrupted. This is not a pass.'}</p>
              {review.report?.decision && <p><strong>{review.report.decision.replaceAll('_', ' ')}</strong></p>}
              {review.report?.visual?.findings?.map((finding, index) => <p key={index}><strong>{finding.severity} · {finding.category.replaceAll('_', ' ')}</strong>: {finding.description}</p>)}
              {review.report?.visual?.manual_review?.map((item, index) => <p className="cf-notice" key={index}>{item}</p>)}
              {review.report?.limitations?.map((item, index) => <small className="cf-review-limit" key={index}>{item}</small>)}
              {review.state === 'failed' && <button className="cf-secondary" disabled={reviewing !== null} onClick={() => reviewClip(review.clip_index, true)}>Retry failed review</button>}
              {review.report?.decision === 'review_required' && review.report?.repair_supported && review.report?.visual?.repair && <button className="cf-primary" disabled={busy} onClick={makeRepairPlan}>Build bounded repair plan</button>}
            </div>}
          </section>}
          {editPlan && <section className="cf-results cf-panel cf-plan">
            <div className="cf-section-heading"><h2>Edit plan <span>Revision {editPlan.revision}</span></h2><span className="cf-status">{planDirty ? 'Unsaved revision' : 'Pinned'}</span></div>
            <p className="cf-hint">Exact source cuts in milliseconds. Saving creates a new revision; previous plans stay unchanged. Source SHA-256: {editPlan.source_sha256.slice(0, 16)}…</p>
            <div className="cf-field-grid"><label>Style<select value={editPlan.style} onChange={e => { setEditPlan(old => ({ ...old, style: e.target.value })); setPlanDirty(true); setRenderSubmission(null) }}>{presets.map(item => <option value={item.id} key={item.id}>{item.name}</option>)}</select></label><label className="cf-check"><input type="checkbox" checked={editPlan.captions} onChange={e => { setEditPlan(old => ({ ...old, captions: e.target.checked })); setPlanDirty(true); setRenderSubmission(null) }} />Styled captions</label></div>
            {editPlan.clips.map((clip, index) => <div className="cf-plan-cut" key={clip.candidate_index}>
              <div className="cf-label-row"><strong>Candidate {clip.candidate_index + 1}</strong><button className="cf-text-button" disabled={editPlan.clips.length === 1} onClick={() => { setEditPlan(old => ({ ...old, clips: old.clips.filter((_, position) => position !== index) })); setPlanDirty(true); setRenderSubmission(null) }}>Remove</button></div>
              <div className="cf-field-grid"><label>Start (ms)<input type="number" min="0" step="1" value={clip.start_ms} onChange={e => changeCut(index, 'start_ms', Number(e.target.value))} /></label><label>End (ms)<input type="number" min="1" step="1" value={clip.end_ms} onChange={e => changeCut(index, 'end_ms', Number(e.target.value))} /></label></div>
              <label>Title<input value={clip.title} maxLength={200} onChange={e => changeCut(index, 'title', e.target.value)} /></label>
              <label>Opening hook<input value={clip.hook} maxLength={160} onChange={e => changeCut(index, 'hook', e.target.value)} /></label>
            </div>)}
            <div className="cf-plan-actions"><button className="cf-secondary" disabled={busy || !planDirty} onClick={savePlan}>Save new revision</button><button className="cf-primary" disabled={busy || planDirty} onClick={renderPlan}>{busy ? <Loader2 size={15} className="cf-spin" /> : <Clapperboard size={15} />}Render {editPlan.clips.length} planned clips</button></div>
            <p className="cf-hint">Rendering reuses the retained source and transcript. No second download or candidate-selection call. Review framing and creative quality on the rendered result.</p>
          </section>}
        </>}
        {tab === 'styles' && <><div className="cf-heading"><div><p className="cf-kicker">A LOOK WITH INTENTION</p><h1>Find the right feeling.</h1><p>Six versioned directions. Choose based on the footage.</p></div></div><div className="cf-preset-grid">{presets.map(item => <button className="cf-panel cf-preset-card" key={item.id} onClick={() => { update('style', item.id); setTab('productions') }}><div className="cf-mini-preview" style={{ '--preview-accent': item.accent }}><span>{item.theme}</span><strong>Make it<br /><em>mean something.</em></strong><span>TYPOGRAPHY CONCEPT</span></div><div><h2>{item.name} <ArrowUpRight size={16} /></h2><p>{item.description}</p><small>{item.caption.font_name} · {item.caption.font_size}px · v{item.version}</small></div></button>)}</div></>}
        {tab === 'providers' && <><div className="cf-heading"><div><p className="cf-kicker">BRING YOUR OWN INTELLIGENCE</p><h1>The right model for the job.</h1><p>Configure providers once. Your agents use the same connection.</p></div></div><p className="cf-hint">Saved keys are encrypted locally and never returned. “Configured” does not mean the provider has been tested. Text and vision capabilities are configured separately.</p><div className="cf-provider-grid">{providers.map(item => <ProviderCard key={item.kind + ':' + item.updated} config={item} onSave={saveProvider} onRemove={removeProvider} />)}</div></>}
        {tab === 'agents' && <><div className="cf-heading"><div><p className="cf-kicker">BUILT TO WORK TOGETHER</p><h1>Your agent. Our workspace.</h1><p>Connect any MCP client to the same production tools.</p></div></div><div className="cf-panel cf-agent-panel"><Workflow size={28} /><h2>One connection. A complete handoff.</h2><p>Give your agent a source, campaign brief and style. It can analyze footage, choose a direction, create or revise exact edit plans, render retained sources, inspect results, and publish with the right scope.</p><div className="cf-label-row"><span>MCP connection example</span><button className="cf-text-button" onClick={async () => { try { await navigator.clipboard.writeText(config); setCopied(true) } catch (_) { setError('Clipboard unavailable; select and copy the configuration below.') } }}>{copied ? <Check size={14} /> : <Copy size={14} />}{copied ? 'Copied' : 'Copy'}</button></div><pre className="cf-code">{config}</pre><p className="cf-hint">Client configuration formats vary. Use a scoped agent token from your server configuration. Give publishing access only to agents that need it.</p><div className="cf-agent-flow"><span>Source + brief</span><ChevronRight size={14} /><span>Produce</span><ChevronRight size={14} /><span>Inspect + review</span><ChevronRight size={14} /><span>Publish</span></div></div></>}
      </div>
    </main>
  </div>
}



function ProviderCard({ config, onSave, onRemove }) {
  const names = {
    gemini: ['Google Gemini', 'Native text selection and upstream vision/layout stages.'],
    text: ['Compatible text model', 'Any configured OpenAI-compatible chat endpoint.'],
    vision: ['Compatible vision model', 'Footage direction and sampled-frame review. Requires image input support.'],
    upload_post: ['Upload-Post', 'Connected social accounts and publishing.'],
    elevenlabs: ['ElevenLabs', 'Voice generation and dubbing.'],
    fal: ['fal.ai', 'Image and video generation in the existing editor.'],
  }
  const [enabled, setEnabled] = useState(config.enabled)
  const [endpoint, setEndpoint] = useState(config.endpoint || '')
  const [model, setModel] = useState(config.model || '')
  const [token, setToken] = useState('')
  const [saving, setSaving] = useState(false)
  const compatible = config.kind === 'text' || config.kind === 'vision'
  async function save(event) {
    event.preventDefault()
    setSaving(true)
    try {
      await onSave(config.kind, { enabled, endpoint: compatible ? endpoint : null,
        model: compatible || config.kind === 'gemini' ? model : null, profile: config.kind === 'upload_post' ? model : null, token: token.trim() || null })
      setToken('')
    } catch (_) { /* parent shows the server error */ }
    finally { setSaving(false) }
  }
  async function remove() {
    setSaving(true)
    try { await onRemove(config.kind) }
    catch (_) { /* parent shows the server error */ }
    finally { setSaving(false) }
  }
  return <form className="cf-panel cf-provider" onSubmit={save}>
    <div className="cf-section-heading"><h2>{names[config.kind]?.[0] || config.kind}</h2><span className="cf-status">{!config.enabled ? 'Disabled' : config.token_configured || compatible && config.endpoint ? 'Configured' : 'Not configured'}</span></div>
    <p>{names[config.kind]?.[1]}</p>
    {compatible && <label>API base URL<input type="url" required={enabled} value={endpoint} onChange={e => setEndpoint(e.target.value)} placeholder="https://your-provider.example/v1" autoComplete="off" /></label>}
    {(compatible || config.kind === 'gemini') && <label>Model ID<input required={compatible && enabled} value={model} onChange={e => setModel(e.target.value)} placeholder="Exact model ID from your provider" autoComplete="off" /></label>}
    {config.kind === 'upload_post' && <label>Default social profile<input value={model} onChange={e => setModel(e.target.value)} placeholder="Upload-Post profile username" autoComplete="off" /></label>}
    <label>API key<input type="password" value={token} onChange={e => setToken(e.target.value)} autoComplete="off" placeholder={config.token_configured ? 'Key saved · leave blank to keep it' : compatible ? 'Optional for local endpoints' : 'Paste your API key'} /></label>
    <label className="cf-check"><input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} />Use this provider configuration</label>
    <div className="cf-plan-actions"><button type="button" className="cf-text-button" disabled={saving || config.source !== 'vault'} onClick={remove}>Use deployment environment</button><button className="cf-primary" disabled={saving}>{saving ? <Loader2 size={14} className="cf-spin" /> : <KeyRound size={14} />}Save connection</button></div>
  </form>
}
