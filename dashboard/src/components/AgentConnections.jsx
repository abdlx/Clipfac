import { useCallback, useEffect, useState } from 'react'
import { Check, ChevronRight, Copy, KeyRound, Plus, Trash2, Workflow } from 'lucide-react'
import { apiJson } from '../lib/api'

const SCOPE_INFO = [
  { value: 'read', label: 'Read', hint: 'List productions, check status, read transcripts and reviews' },
  { value: 'process', label: 'Process', hint: 'Analyze videos, produce clips, render edit plans' },
  { value: 'edit', label: 'Edit', hint: 'Trim, split, merge and style clips; create and revise plans' },
  { value: 'publish', label: 'Publish', hint: 'Post reviewed clips to connected accounts' },
]

function formatTime(ts) {
  if (!ts) return 'never'
  try { return new Date(ts * 1000).toLocaleString() } catch (_) { return '—' }
}

export default function AgentConnections({ onError }) {
  const [tokens, setTokens] = useState(null)
  const [name, setName] = useState('')
  const [scopes, setScopes] = useState(['read', 'process'])
  const [creating, setCreating] = useState(false)
  const [newToken, setNewToken] = useState(null)
  const [copied, setCopied] = useState(false)
  const [copiedConfig, setCopiedConfig] = useState(false)
  const [revoking, setRevoking] = useState(null)

  const load = useCallback(async () => {
    try {
      const data = await apiJson('/api/clipfac/agent-tokens')
      setTokens(data.tokens || [])
    } catch (err) {
      onError?.(err.detail || 'Could not load agent tokens.')
      setTokens([])
    }
  }, [onError])

  useEffect(() => { load() }, [load])

  const toggleScope = (value) => {
    setScopes((prev) => prev.includes(value) ? prev.filter((s) => s !== value) : [...prev, value])
  }

  const create = async () => {
    if (!name.trim() || scopes.length === 0 || creating) return
    setCreating(true)
    try {
      const record = await apiJson('/api/clipfac/agent-tokens', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name.trim(), scopes }),
      })
      setNewToken(record)
      setName('')
      await load()
    } catch (err) {
      onError?.(err.detail || 'Could not create the token.')
    } finally {
      setCreating(false)
    }
  }

  const revoke = async (id) => {
    setRevoking(id)
    try {
      await apiJson(`/api/clipfac/agent-tokens/${id}`, { method: 'DELETE' })
      await load()
    } catch (err) {
      onError?.(err.detail || 'Could not revoke the token.')
    } finally {
      setRevoking(null)
    }
  }

  const copyText = async (text, setFlag) => {
    try {
      await navigator.clipboard.writeText(text)
      setFlag(true)
      setTimeout(() => setFlag(false), 1600)
    } catch (_) {
      onError?.('Clipboard unavailable; select and copy the text below.')
    }
  }

  const mcpUrl = `${window.location.origin}/mcp`
  const configToken = newToken?.token || 'YOUR_AGENT_TOKEN'
  const config = JSON.stringify({ mcpServers: { clipfac: { url: mcpUrl, headers: { Authorization: `Bearer ${configToken}` } } } }, null, 2)

  return (
    <>
      <div className="cf-heading">
        <div>
          <p className="cf-kicker">BUILT TO WORK TOGETHER</p>
          <h1>Your agent. Our workspace.</h1>
          <p>Connect any MCP client to the same production tools.</p>
        </div>
      </div>
      <div className="cf-panel cf-agent-panel">
        <Workflow size={28} />
        <h2>One connection. A complete handoff.</h2>
        <p>Give your agent a source, campaign brief and style. It can analyze footage, choose a direction, create or revise exact edit plans, render retained sources, inspect results, and publish with the right scope.</p>

        <div className="cf-label-row">
          <span>MCP connection example</span>
          <button className="cf-text-button" onClick={() => copyText(config, setCopiedConfig)}>
            {copiedConfig ? <Check size={14} /> : <Copy size={14} />}{copiedConfig ? 'Copied' : 'Copy'}
          </button>
        </div>
        <pre className="cf-code">{config}</pre>
        <p className="cf-hint">Client configuration formats vary. Paste a token created below in place of YOUR_AGENT_TOKEN. Give publishing access only to agents that need it.</p>
        <div className="cf-agent-flow">
          <span>Source + brief</span><ChevronRight size={14} />
          <span>Produce</span><ChevronRight size={14} />
          <span>Inspect + review</span><ChevronRight size={14} />
          <span>Publish</span>
        </div>
      </div>

      <div className="cf-panel cf-agent-panel" style={{ marginTop: 18 }}>
        <KeyRound size={28} />
        <h2>Agent tokens.</h2>
        <p>One token per agent, with only the scopes it needs. The value is shown once, when it is created — copy it then. Revoking a token stops that agent immediately; your other tokens keep working.</p>

        {newToken && (
          <div style={{ marginBottom: 18 }}>
            <div className="cf-label-row">
              <span>Your new token — copy it now, it will not be shown again</span>
              <button className="cf-text-button" onClick={() => copyText(newToken.token, setCopied)}>
                {copied ? <Check size={14} /> : <Copy size={14} />}{copied ? 'Copied' : 'Copy'}
              </button>
            </div>
            <pre className="cf-code" style={{ borderColor: 'var(--color-accent)' }}>{newToken.token}</pre>
            <p className="cf-hint">MCP: <code>claude mcp add --transport http clipfac {mcpUrl} --header &quot;Authorization: Bearer {newToken.token}&quot;</code></p>
            <button className="cf-text-button" onClick={() => setNewToken(null)}>I saved it — hide this</button>
          </div>
        )}

        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 12 }}>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Token name — e.g. hob Main"
            maxLength={80}
            style={{ flex: '1 1 220px' }}
            aria-label="Token name"
          />
          <button className="btn-primary" onClick={create} disabled={!name.trim() || scopes.length === 0 || creating}>
            <Plus size={14} /> {creating ? 'Creating…' : 'Create token'}
          </button>
        </div>
        <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', marginBottom: 18 }}>
          {SCOPE_INFO.map((s) => (
            <label key={s.value} style={{ display: 'flex', gap: 6, alignItems: 'flex-start', fontSize: 12, color: 'var(--color-muted)', cursor: 'pointer' }} title={s.hint}>
              <input type="checkbox" checked={scopes.includes(s.value)} onChange={() => toggleScope(s.value)} style={{ marginTop: 2 }} />
              <span><strong style={{ color: 'var(--color-ink)' }}>{s.label}</strong> — {s.hint}</span>
            </label>
          ))}
        </div>

        {tokens === null ? (
          <p className="cf-hint">Loading tokens…</p>
        ) : tokens.length === 0 ? (
          <p className="cf-hint">No agent tokens yet. Create one above — it takes ten seconds, no server restart.</p>
        ) : (
          <div style={{ display: 'grid', gap: 10 }}>
            {tokens.map((t) => (
              <div key={t.id} style={{ display: 'flex', gap: 12, alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', border: '1px solid var(--color-rule)', borderRadius: 12, padding: '12px 14px' }}>
                <div>
                  <div style={{ fontSize: 13, fontWeight: 600 }}>{t.name}</div>
                  <div style={{ fontSize: 11, color: 'var(--color-muted)', fontFamily: 'var(--font-mono)' }}>{t.prefix}…</div>
                  <div style={{ fontSize: 11, color: 'var(--color-muted)' }}>
                    {t.scopes.join(', ')} · created {formatTime(t.created)} · last used {formatTime(t.last_used)}
                  </div>
                </div>
                <button
                  className="cf-text-button"
                  onClick={() => revoke(t.id)}
                  disabled={revoking === t.id}
                  style={{ color: 'var(--color-danger, #c0392b)' }}
                >
                  <Trash2 size={14} /> {revoking === t.id ? 'Revoking…' : 'Revoke'}
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </>
  )
}
