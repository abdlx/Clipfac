import { useEffect, useState } from 'react'
import ProviderCard from './ProviderCard.jsx'
import AgentConnections from './AgentConnections.jsx'

const LAYOUT_OPTIONS = [
  { value: 'auto', label: 'Auto (AI picks per video)' },
  { value: 'split', label: 'Two speakers stacked' },
  { value: 'screencast', label: 'Screen over presenter' },
  { value: 'none', label: 'Single crop only' },
]

const HOOK_STYLES = [
  { value: 'pill', label: 'Pills' },
  { value: 'classic', label: 'Classic' },
  { value: 'dark', label: 'Dark' },
  { value: 'yellow', label: 'Yellow' },
  { value: 'red', label: 'Red' },
  { value: 'outline', label: 'Outline' },
  { value: 'outline_yellow', label: 'Outline+' },
]

function readPref(key, fallback) {
  try { return localStorage.getItem(key) ?? fallback } catch (_) { return fallback }
}
function writePref(key, value) {
  try { localStorage.setItem(key, value) } catch (_) { /* storage unavailable */ }
}

/**
 * The one settings surface. Provider connections (the vault — the only
 * place keys are saved), agent connections, and editor preferences.
 * The editor used to ask for the same keys again in its own settings tab
 * and inline inputs; those are gone — everything reads from the vault.
 */
export default function SettingsView({ providers, onSaveProvider, onRemoveProvider, onError }) {
  const [layout, setLayout] = useState(() => readPref('os_layout', 'auto'))
  const [autoHook, setAutoHook] = useState(() => readPref('os_auto_hook', '1') !== '0')
  const [autoHookStyle, setAutoHookStyle] = useState(() => readPref('os_auto_hook_style_v2', 'pill'))

  useEffect(() => { writePref('os_layout', layout) }, [layout])
  useEffect(() => { writePref('os_auto_hook', autoHook ? '1' : '0') }, [autoHook])
  useEffect(() => { writePref('os_auto_hook_style_v2', autoHookStyle) }, [autoHookStyle])

  return (
    <>
      <div className="cf-heading">
        <div>
          <p className="cf-kicker">ONE PLACE FOR EVERYTHING</p>
          <h1>Settings.</h1>
          <p>Providers, agents and editor preferences — saved once, used everywhere.</p>
        </div>
      </div>

      <div className="cf-heading">
        <div>
          <p className="cf-kicker">BRING YOUR OWN INTELLIGENCE</p>
          <h1>The right model for the job.</h1>
          <p>Configure providers once. Your agents use the same connection.</p>
        </div>
      </div>
      <p className="cf-hint" style={{ maxWidth: 1120, margin: '0 auto 20px' }}>Saved keys are encrypted locally and never returned. “Configured” does not mean the provider has been tested. Text and vision capabilities are configured separately.</p>
      {providers.length === 0 ? (
        <div className="cf-empty" style={{ maxWidth: 1120, margin: '0 auto 30px' }}>
          <h3>Loading providers…</h3>
          <p>If this stays empty, check the connection and reload.</p>
        </div>
      ) : (
        <div className="cf-provider-grid" style={{ marginBottom: 40 }}>
          {providers.map((config) => (
            <ProviderCard key={config.kind + ':' + config.updated} config={config} onSave={onSaveProvider} onRemove={onRemoveProvider} />
          ))}
        </div>
      )}

      <AgentConnections onError={onError} />

      <div className="cf-heading" style={{ marginTop: 40, marginBottom: 16 }}>
        <div>
          <p className="cf-kicker">EDITOR PREFERENCES</p>
          <h2 style={{ fontSize: 20, margin: 0, letterSpacing: '-.5px' }}>Clip generation</h2>
          <p>These apply to every clip the editor generates. They live in this browser.</p>
        </div>
      </div>
      <div className="cf-panel cf-provider" style={{ maxWidth: 1120, margin: '0 auto 40px' }}>
        <label>Vertical layout
          <select value={layout} onChange={(e) => setLayout(e.target.value)}>
            {LAYOUT_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </label>
        <label className="cf-check">
          <input type="checkbox" checked={autoHook} onChange={(e) => setAutoHook(e.target.checked)} />
          <span>Auto hook titles on clips</span>
        </label>
        {autoHook && (
          <label>Hook style
            <select value={autoHookStyle} onChange={(e) => setAutoHookStyle(e.target.value)}>
              {HOOK_STYLES.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </label>
        )}
        {autoHook && (
          <p className="cf-hint" style={{ marginBottom: 0 }}>
            You can change each clip&apos;s hook text, style, position and size afterwards with its hook button.
          </p>
        )}
      </div>

      <div className="cf-heading" style={{ marginTop: 40, marginBottom: 16 }}>
        <div>
          <p className="cf-kicker">ABOUT</p>
          <h2 style={{ fontSize: 20, margin: 0, letterSpacing: '-.5px' }}>Self-hosted workspace</h2>
          <p>
            This is your own Clipfac install. Your footage, your providers, your keys — nothing leaves this server
            except the provider calls you configure above. The workspace signs in with the installation token;
            there is no per-user account.
          </p>
        </div>
      </div>
    </>
  )
}
