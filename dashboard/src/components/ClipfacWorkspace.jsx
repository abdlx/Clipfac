import { useCallback, useEffect, useState } from 'react'
import { ArrowUpRight, Check, ChevronRight, Clapperboard, Copy, Film, KeyRound, Loader2, Moon, Plus, RefreshCw, Settings2, ShieldCheck, SlidersHorizontal, Sparkles, Sun, Workflow } from 'lucide-react'
import { apiJson } from '../lib/api'
import { getApiUrl } from '../config'

const initial = { source_url: '', style: 'podcast', campaign_name: '', platform: 'independent', audience: '', objective: '', notes: '', required: '', forbidden: '', minimum_score: 70, min_seconds: 15, max_seconds: 60, max_clips: 15, confirm_rights: false, captions: true }
const lines = value => value.split('\n').map(x => x.trim()).filter(Boolean)
const errorText = error => error.detail || error.message || 'Request failed'

export default function ClipfacWorkspace() {
  const [tab, setTab] = useState('productions')
  const [presets, setPresets] = useState([])
  const [form, setForm] = useState(initial)
  const [productions, setProductions] = useState([])
  const [selected, setSelected] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [locked, setLocked] = useState(false)
  const [token, setToken] = useState('')
  const [theme, setTheme] = useState(() => localStorage.getItem('clipfac_theme') || 'light')
  const [report, setReport] = useState(null)
  const [inspecting, setInspecting] = useState(null)
  const [copied, setCopied] = useState(false)
  const [submission, setSubmission] = useState(null)

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    localStorage.setItem('clipfac_theme', theme)
  }, [theme])

  const refresh = useCallback(async () => {
    try {
      const [styles, history] = await Promise.all([
        apiJson('/api/clipfac/presets'), apiJson('/api/clipfac/productions'),
      ])
      setPresets(styles.presets)
      setProductions(history.productions)
      setLocked(false)
    } catch (err) {
      if (err.status === 401) setLocked(true)
      else setError(errorText(err))
    }
  }, [])

  useEffect(() => { refresh() }, [refresh])

  const selectedId = selected?.id
  const running = selected && !['completed', 'failed', 'blocked', 'uncertain', 'unavailable'].includes(selected.state)
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
    setBusy(true); setError(''); setReport(null)
    // Hold this exact key + body across ambiguous failures. Editing the form starts a new request.
    const body = submission || { ...payload(), idempotency_key: crypto.randomUUID() }
    setSubmission(body)
    try {
      const data = await apiJson('/api/clipfac/productions', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      setSelected(data)
      await refresh()
    } catch (err) { setError(errorText(err)) }
    finally { setBusy(false) }
  }
  async function openProduction(id) {
    setBusy(true); setError(''); setReport(null)
    try { setSelected(await apiJson('/api/clipfac/productions/' + id)) }
    catch (err) { setError(errorText(err)) }
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
        {[['productions', Film, 'Productions'], ['styles', SlidersHorizontal, 'Style library'], ['agents', Workflow, 'Agent connections']].map(([id, Icon, label]) =>
          <button key={id} aria-current={tab === id ? 'page' : undefined} className={tab === id ? 'cf-nav active' : 'cf-nav'} onClick={() => { setTab(id); setError('') }}><Icon size={18} />{label}{tab === id && <span className="cf-nav-dot" />}</button>)}
      </nav>
      <div className="cf-sidebar-bottom">
        <div className="cf-local"><span /> Self-hosted workspace<small>Your footage. Your providers.</small></div>
        <a className="cf-nav" href="#app?tab=settings"><Settings2 size={18} />Provider settings<ArrowUpRight size={14} /></a>
        <button className="cf-nav" onClick={() => setTheme(theme === 'light' ? 'dark' : 'light')}>{theme === 'light' ? <Moon size={18} /> : <Sun size={18} />}{theme === 'light' ? 'Dark appearance' : 'Light appearance'}</button>
      </div>
    </aside>
    <main className="cf-main">
      <header className="cf-topbar"><span>Workspace <ChevronRight size={14} /> <strong>{tab === 'productions' ? 'Productions' : tab === 'styles' ? 'Style library' : 'Agent connections'}</strong></span><a href="#app">Open editor <ArrowUpRight size={14} /></a></header>
      <div className="cf-content">
        {error && <div className="cf-error" role="alert">{error}<button onClick={() => setError('')} aria-label="Dismiss error">×</button></div>}
        {tab === 'productions' && <>
          <div className="cf-heading"><div><p className="cf-kicker">FROM LONG FORM TO YOUR NEXT STORY</p><h1>A little footage.<br /><span>A lot of possibility.</span></h1><p>Give every worthwhile moment its own frame.</p></div><button className="cf-secondary" onClick={() => { setSelected(null); setReport(null) }}><Plus size={16} />New production</button></div>
          <div className="cf-grid">
            <form className="cf-panel cf-production-form" onSubmit={produce}>
              <div className="cf-panel-heading"><span className="cf-icon"><Clapperboard size={19} /></span><div><h2>Create a production</h2><p>Source, direction, and a look that fits.</p></div></div>
              <label>Video link<input type="url" required value={form.source_url} onChange={e => update('source_url', e.target.value)} placeholder="Paste a YouTube or direct video link" /></label>
              <div className="cf-label-row"><span>Editing style</span><button type="button" className="cf-text-button" onClick={() => setTab('styles')}>Explore styles <ArrowUpRight size={12} /></button></div>
              <div className="cf-style-options">{presets.map(item => <button type="button" key={item.id} aria-pressed={form.style === item.id} className={'cf-style-chip ' + (form.style === item.id ? 'selected' : '')} onClick={() => update('style', item.id)}><i style={{ background: item.accent }} />{item.name}{form.style === item.id && <Check size={12} />}</button>)}</div>
              <p className="cf-hint">{preset?.description || 'Loading style settings…'}</p>
              <details className="cf-details"><summary>Campaign brief <span>Optional</span></summary>
                <div className="cf-field-grid"><label>Campaign name<input value={form.campaign_name} onChange={e => update('campaign_name', e.target.value)} placeholder="Product launch" /></label><label>Source platform<select value={form.platform} onChange={e => update('platform', e.target.value)}><option value="independent">Independent</option><option value="whop">Whop</option><option value="tryshoot">Tryshoot</option><option value="other">Other</option></select></label></div>
                <label>Audience<input value={form.audience} onChange={e => update('audience', e.target.value)} placeholder="Who should this speak to?" /></label>
                <label>Objective<textarea value={form.objective} onChange={e => update('objective', e.target.value)} placeholder="The message or outcome this campaign needs." rows={2} /></label>
                <div className="cf-field-grid"><label>Required spoken phrases<textarea value={form.required} onChange={e => update('required', e.target.value)} placeholder="One phrase per line" rows={2} /></label><label>Forbidden spoken phrases<textarea value={form.forbidden} onChange={e => update('forbidden', e.target.value)} placeholder="One phrase per line" rows={2} /></label></div>
                <label>Editorial notes<textarea value={form.notes} onChange={e => update('notes', e.target.value)} placeholder="Brand rules, approved claims, context to preserve…" rows={2} /></label>
                <p className="cf-hint">Paste the campaign rules here. Clipfac does not fetch campaign briefs from Whop or Tryshoot.</p>
              </details>
              <details className="cf-details"><summary>Production controls <span>Quality over quantity</span></summary>
                <div className="cf-field-grid cf-three"><label>Minimum seconds<input type="number" min="5" max="175" value={form.min_seconds} onChange={e => update('min_seconds', e.target.value)} /></label><label>Maximum seconds<input type="number" min="10" max="180" value={form.max_seconds} onChange={e => update('max_seconds', e.target.value)} /></label><label>Clip ceiling<input type="number" min="1" max="15" value={form.max_clips} onChange={e => update('max_clips', e.target.value)} /></label></div>
                <label>Minimum editorial score: {form.minimum_score}/100<input type="range" min="0" max="100" value={form.minimum_score} onChange={e => update('minimum_score', e.target.value)} /></label>
                <label className="cf-check"><input type="checkbox" checked={form.captions} onChange={e => update('captions', e.target.checked)} />Burn styled captions</label>
              </details>
              <label className="cf-check"><input type="checkbox" required checked={form.confirm_rights} onChange={e => update('confirm_rights', e.target.checked)} />I own this content or have permission to process it.</label>
              <button className="cf-primary cf-wide" disabled={busy || !presets.length}>{busy ? <Loader2 className="cf-spin" size={17} /> : <Sparkles size={17} />}Create clips<ArrowUpRight size={16} /></button>
              <p className="cf-footnote">Only distinct moments above your threshold are shortlisted. Reach is never guaranteed.</p>
            </form>
            <aside className="cf-right-column">
              <div className="cf-style-preview" style={{ '--preview-accent': preset?.accent || '#82B2FF' }}><div className="cf-preview-top"><span>YOUR DIRECTION</span><span>9:16</span></div><div className="cf-preview-frame"><div className="cf-preview-bars"><i /><i /><i /></div><div className="cf-preview-caption">Every moment<br />has <em>a story.</em></div><span className="cf-preview-stamp">TYPOGRAPHY CONCEPT · NOT A VIDEO PREVIEW</span></div><div className="cf-preview-bottom"><strong>{preset?.name || 'Editorial'}</strong><small>Version {preset?.version || 1} · {preset?.theme || 'podcast'}</small></div></div>
              <div className="cf-panel cf-how"><ShieldCheck size={20} /><h3>A considered workflow.</h3><p>Campaign direction informs selection. The renderer applies your style. Inspect the result before publishing.</p><a href="#app?tab=settings">Connect your providers <ArrowUpRight size={13} /></a></div>
            </aside>
          </div>
          <section className="cf-history"><div className="cf-section-heading"><h2>Recent productions <span>{productions.length}</span></h2><button className="cf-text-button" onClick={refresh}><RefreshCw size={14} />Refresh</button></div>
            {productions.length ? <div className="cf-production-list">{productions.map(item => <button className={'cf-history-row ' + (selected?.id === item.id ? 'selected' : '')} key={item.id} onClick={() => openProduction(item.id)}><span className="cf-history-icon"><Film size={17} /></span><span><strong>{item.request.campaign.name || item.request.source_url || 'Uploaded video'}</strong><small>{item.request.style} · {new Date(item.created * 1000).toLocaleString()}</small></span><span className="cf-status">{selected?.id === item.id ? selected.state : item.state}</span><ChevronRight size={16} /></button>)}</div> : <div className="cf-empty"><Film size={24} /><h3>Your next production starts here.</h3><p>Add a video above, or let an agent bring one in.</p></div>}
          </section>
          {selected && <section className="cf-results"><div className="cf-section-heading"><h2>Production results</h2><span className="cf-status">{selected.state}</span></div>
            {selected.state === 'submitting' && <p className="cf-notice">Submission is in progress or was interrupted. Reusing the request will not start duplicate work. Check the instance before submitting again.</p>}
            {selected.state === 'uncertain' && <p className="cf-notice">Submission was interrupted. Check processing jobs before starting another request.</p>}
            {selected.state === 'blocked' && <pre className="cf-code">{JSON.stringify(selected.response, null, 2)}</pre>}
            {selected.state === 'failed' && <p className="cf-error">Processing failed. Review the latest job logs below.</p>}
            {selected.job?.logs?.length > 0 && <details className="cf-details"><summary>Processing log</summary><pre className="cf-code">{selected.job.logs.slice(-20).join('\n')}</pre></details>}
            {selected.selection && <><p className="cf-hint">{selected.selection.accepted.length} shortlisted · {selected.selection.held.length} held for review. {selected.selection.review_notice}</p><div className="cf-clip-grid">{selected.selection.accepted.map(({ index, score, clip }) => <article className="cf-panel cf-clip" key={index}><video src={getApiUrl(clip.video_url)} controls preload="metadata" playsInline /><div><span className="cf-kicker">CLIP {index + 1} · EDITORIAL SCORE {score}</span><h3>{clip.title || clip.video_title_for_youtube_short || 'Untitled moment'}</h3><button className="cf-secondary" disabled={inspecting !== null} onClick={() => inspect(index)}>{inspecting === index ? <Loader2 size={14} className="cf-spin" /> : <ShieldCheck size={14} />}Inspect render</button><a href={getApiUrl(clip.download_url || clip.video_url)} download>Download <ArrowUpRight size={13} /></a></div></article>)}</div>{selected.selection.held.length > 0 && <details className="cf-details"><summary>Held clips and reasons</summary><ul>{selected.selection.held.map(item => <li key={item.index}>Clip {item.index + 1}: {item.reasons.join(', ').replaceAll('_', ' ')}</li>)}</ul></details>}</>}
            {report && <div className="cf-panel cf-report"><h3>Clip {report.index + 1}: {report.status.replaceAll('_', ' ')}</h3><p>{report.width} × {report.height} · {report.duration_seconds?.toFixed(1)}s · Audio {report.audio ? 'present' : 'missing'}</p><p>{report.issues.length ? report.issues.join(', ').replaceAll('_', ' ') : 'No defects detected by the implemented technical checks.'}</p><small>Still requires review: {report.not_checked.join(', ').replaceAll('_', ' ')}.</small></div>}
          </section>}
        </>}
        {tab === 'styles' && <><div className="cf-heading"><div><p className="cf-kicker">A LOOK WITH INTENTION</p><h1>Find the right feeling.</h1><p>Six versioned directions. Choose based on the footage.</p></div></div><div className="cf-preset-grid">{presets.map(item => <button className="cf-panel cf-preset-card" key={item.id} onClick={() => { update('style', item.id); setTab('productions') }}><div className="cf-mini-preview" style={{ '--preview-accent': item.accent }}><span>{item.theme}</span><strong>Make it<br /><em>mean something.</em></strong><span>TYPOGRAPHY CONCEPT</span></div><div><h2>{item.name} <ArrowUpRight size={16} /></h2><p>{item.description}</p><small>{item.caption.font_name} · {item.caption.font_size}px · v{item.version}</small></div></button>)}</div></>}
        {tab === 'agents' && <><div className="cf-heading"><div><p className="cf-kicker">BUILT TO WORK TOGETHER</p><h1>Your agent. Our workspace.</h1><p>Connect any MCP client to the same production tools.</p></div></div><div className="cf-panel cf-agent-panel"><Workflow size={28} /><h2>One connection. A complete handoff.</h2><p>Give your agent a source, campaign brief and style. It can start production, inspect transcripts and scenes, shortlist clips, and request technical inspection.</p><div className="cf-label-row"><span>MCP connection example</span><button className="cf-text-button" onClick={async () => { try { await navigator.clipboard.writeText(config); setCopied(true) } catch (_) { setError('Clipboard unavailable; select and copy the configuration below.') } }}>{copied ? <Check size={14} /> : <Copy size={14} />}{copied ? 'Copied' : 'Copy'}</button></div><pre className="cf-code">{config}</pre><p className="cf-hint">Client configuration formats vary. Use a scoped agent token from your server configuration. Give publishing access only to agents that need it.</p><div className="cf-agent-flow"><span>Source + brief</span><ChevronRight size={14} /><span>Produce</span><ChevronRight size={14} /><span>Inspect + review</span><ChevronRight size={14} /><span>Publish</span></div></div></>}
      </div>
    </main>
  </div>
}
