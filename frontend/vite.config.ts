import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  build: {
    // lightningcss has a known issue minifying certain webkit pseudo-element selectors;
    // disable CSS minification to avoid the whitespace-in-pseudo-element error.
    cssMinify: false,
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
