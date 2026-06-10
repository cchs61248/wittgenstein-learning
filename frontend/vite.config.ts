import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Proxy 目標：本機開發預設打 127.0.0.1:8000；在 Docker 內由 compose 設
// VITE_PROXY_TARGET=http://api:8000（容器網路無法用 127.0.0.1 連到 api 容器）。
const proxyTarget = process.env.VITE_PROXY_TARGET || 'http://127.0.0.1:8000'
const wsProxyTarget = proxyTarget.replace(/^http/, 'ws')

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
      '/auth': { target: proxyTarget, changeOrigin: true },
      '/upload': { target: proxyTarget, changeOrigin: true },
      '/sessions': { target: proxyTarget, changeOrigin: true },
      '/learner': { target: proxyTarget, changeOrigin: true },
      '/user': { target: proxyTarget, changeOrigin: true },
      '/health': { target: proxyTarget, changeOrigin: true },
      '/config': { target: proxyTarget, changeOrigin: true },
      '/ws': { target: wsProxyTarget, ws: true, changeOrigin: true },
    },
  },
})
