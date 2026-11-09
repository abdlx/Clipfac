import { StrictMode, Suspense, lazy, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import './clipfac.css'
import { AuthProvider } from './contexts/AuthContext'

const Workspace = lazy(() => import('./components/ClipfacWorkspace'))

// One shell, one URL scheme. The workspace is the shell; the hash only
// picks which view it opens. Old deep links keep working:
//   #app / #/app            -> Clips (the editor's clip generator)
//   #app?tab=saasshorts     -> AI Shorts
//   #app?tab=thumbnails     -> Thumbnails
//   #app?tab=ugc-gallery    -> Gallery
//   #app?tab=settings       -> Settings
//   #workspace / anything else -> Projects
function viewFromHash(hash) {
  if (hash.startsWith('#app') || hash === '#/app') {
    const query = hash.split('?')[1] || ''
    const tab = new URLSearchParams(query).get('tab')
    if (tab === 'saasshorts') return 'shorts'
    if (tab === 'thumbnails') return 'thumbnails'
    if (tab === 'ugc-gallery') return 'gallery'
    if (tab === 'settings') return 'settings'
    return 'clips'
  }
  return 'productions'
}

function Root() {
  const [view, setView] = useState(() => viewFromHash(window.location.hash))
  useEffect(() => {
    const change = () => setView(viewFromHash(window.location.hash))
    window.addEventListener('hashchange', change)
    return () => window.removeEventListener('hashchange', change)
  }, [])
  return <Workspace initialView={view} />
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
