import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    allowedHosts: process.env.VITE_ALLOWED_HOST
      ? [process.env.VITE_ALLOWED_HOST]
      : [],
    watch: {
      usePolling: process.env.DOCKER_WATCH_POLLING === 'true',
      interval: 1000,
    },
    proxy: {
      '/api': {
        target:
          process.env.API_PROXY_TARGET ?? 'http://127.0.0.1:8000',
        // Keep the browser host for authentication origin checks through the proxy.
        changeOrigin: false,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
