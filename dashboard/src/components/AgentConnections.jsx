import { useState } from 'react'
import { Check, ChevronRight, Copy, Workflow } from 'lucide-react'

export default function AgentConnections({ onError }) {
  const [copied, setCopied] = useState(false)
  const config = JSON.stringify({ mcpServers: { clipfac: { url: window.location.origin + '/mcp', headers: { Authorization: 'Bearer YOUR_AGENT_TOKEN' } } } }, null, 2)

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
          <button className="cf-text-button" onClick={async () => { try { await navigator.clipboard.writeText(config); setCopied(true) } catch (_) { onError?.('Clipboard unavailable; select and copy the configuration below.') } }}>
            {copied ? <Check size={14} /> : <Copy size={14} />}{copied ? 'Copied' : 'Copy'}
          </button>
        </div>
        <pre className="cf-code">{config}</pre>
        <p className="cf-hint">Client configuration formats vary. Use a scoped agent token from your server configuration. Give publishing access only to agents that need it.</p>
        <div className="cf-agent-flow">
          <span>Source + brief</span><ChevronRight size={14} />
          <span>Produce</span><ChevronRight size={14} />
          <span>Inspect + review</span><ChevronRight size={14} />
          <span>Publish</span>
        </div>
      </div>
    </>
  )
}
