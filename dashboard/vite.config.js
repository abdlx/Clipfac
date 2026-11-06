import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const backend = process.env.VITE_PROXY_TARGET || 'http://backend:8000'
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: Object.fromEntries(['/api', '/videos', '/thumbnails', '/mcp', '/gallery', '/video'].map(
      path => [path, { target: backend, changeOrigin: false }],
    )),
  },
})
