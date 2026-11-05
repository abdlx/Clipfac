import { StrictMode, Suspense, lazy, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import './clipfac.css'
import { AuthProvider } from './contexts/AuthContext'

const Workspace = lazy(() => import('./components/ClipfacWorkspace'))
const Editor = lazy(() => import('./App.jsx'))

function Root() {
  const [hash, setHash] = useState(window.location.hash)
  useEffect(() => {
    const change = () => setHash(window.location.hash)
    window.addEventListener('hashchange', change)
    return () => window.removeEventListener('hashchange', change)
  }, [])
  return hash.startsWith('#app') || hash === '#/app' ? <Editor /> : <Workspace />
}

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <AuthProvider>
      <Suspense fallback={<div className="cf-loading">Opening Clipfac…</div>}>
        <Root />
      </Suspense>
    </AuthProvider>
  </StrictMode>,
)
