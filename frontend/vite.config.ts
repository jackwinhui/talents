import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Built assets are served by FastAPI at /app, so there is a single origin and
  // the app stays reachable over Tailscale without running a second server.
  base: '/app/',
  build: { outDir: '../backend/talents/static', emptyOutDir: true },
  server: {
    proxy: { '/api': 'http://127.0.0.1:8787', '/assets': 'http://127.0.0.1:8787' },
  },
})
