import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  build: {
    // CSS minification via lightningcss (rolldown-vite default). This was
    // previously disabled because lightningcss crashed on malformed CSS in
    // App.css (an orphaned .quality-warning-banner declaration block whose
    // selector had been deleted). That root cause is now fixed, so the
    // default minifier works again.
    cssMinify: true,
  },
  server: {
    allowedHosts: ['.trycloudflare.com'],
    proxy: {
      '/auth': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/upload': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/sessions': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/learner': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/user': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/health': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/config': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/ws': { target: 'ws://127.0.0.1:8000', ws: true, changeOrigin: true },
    },
  },
})
