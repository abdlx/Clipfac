import { useState } from 'react'
import { KeyRound, Loader2 } from 'lucide-react'

export default function ProviderCard({ config, onSave, onRemove }) {
  const names = {
    gemini: ['Google Gemini', 'Native text selection and upstream vision/layout stages. Leave the base URL blank to use Google, or point it at your own Gemini-compatible endpoint.'],
    text: ['Compatible text model', 'Any configured OpenAI-compatible chat endpoint.'],
    vision: ['Compatible vision model', 'Footage direction and sampled-frame review. Requires image input support.'],
    upload_post: ['Upload-Post', 'Connected social accounts and publishing.'],
    elevenlabs: ['ElevenLabs', 'Voice generation and dubbing.'],
    fal: ['fal.ai', 'Image and video generation in the existing editor.'],
    youtube: ['YouTube cookies (yt-dlp)', 'Paste your YouTube cookies so video downloads use your signed-in session instead of failing YouTube’s bot checks.'],
  }
  const [enabled, setEnabled] = useState(config.enabled)
  const [endpoint, setEndpoint] = useState(config.endpoint || '')
  const [model, setModel] = useState(config.model || '')
  const [token, setToken] = useState('')
  const [saving, setSaving] = useState(false)
  const compatible = config.kind === 'text' || config.kind === 'vision'
  const hasEndpoint = compatible || config.kind === 'gemini'
  async function save(event) {
    event.preventDefault()
    setSaving(true)
    try {
      await onSave(config.kind, { enabled, endpoint: hasEndpoint ? endpoint : null,
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
    {hasEndpoint && <label>API base URL<input type="url" required={compatible && enabled} value={endpoint} onChange={e => setEndpoint(e.target.value)} placeholder={config.kind === 'gemini' ? 'https://your-gemini-endpoint.example' : 'https://your-provider.example/v1'} autoComplete="off" /></label>}
    {config.kind === 'gemini' && <p className="cf-hint">Origin only, no /v1beta suffix — the Gemini client adds the version path itself. Leave blank to use Google.</p>}
    {(compatible || config.kind === 'gemini') && <label>Model ID<input required={compatible && enabled} value={model} onChange={e => setModel(e.target.value)} placeholder="Exact model ID from your provider" autoComplete="off" /></label>}
    {config.kind === 'upload_post' && <label>Default social profile<input value={model} onChange={e => setModel(e.target.value)} placeholder="Upload-Post profile username" autoComplete="off" /></label>}
    {config.kind === 'youtube'
      ? <><label>YouTube cookies (Netscape cookies.txt)<textarea rows={8} value={token} onChange={e => setToken(e.target.value)} autoComplete="off" placeholder={config.token_configured ? 'Cookies saved · paste a fresh export to replace them' : 'Paste the full cookies.txt export for youtube.com'} spellCheck={false} /></label>
        <p className="cf-hint">Export from your browser with the “Get cookies.txt LOCALLY” extension while signed in to YouTube. Cookies expire — if downloads start failing again, paste a fresh export. Stored encrypted, never shown back.</p></>
      : <label>API key<input type="password" value={token} onChange={e => setToken(e.target.value)} autoComplete="off" placeholder={config.token_configured ? 'Key saved · leave blank to keep it' : compatible ? 'Optional for local endpoints' : 'Paste your API key'} /></label>}
    <label className="cf-check"><input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} />Use this provider configuration</label>
    <div className="cf-plan-actions"><button type="button" className="cf-text-button" disabled={saving || config.source !== 'vault'} onClick={remove}>{config.kind === 'youtube' ? 'Remove cookies' : 'Use deployment environment'}</button><button className="cf-primary" disabled={saving}>{saving ? <Loader2 size={14} className="cf-spin" /> : <KeyRound size={14} />}Save connection</button></div>
  </form>
}
