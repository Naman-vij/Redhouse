// vite.config.js
//
// NOTE: the proxy means the page can call "/api/..." and Vite forwards it
// to the Python server on port 8000. The browser only ever talks to one
// address (5173), so there is no cross-origin (CORS) problem while you
// develop. For AWS later, set VITE_API_URL to the real API address instead.

import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/health': 'http://127.0.0.1:8000',
    },
    allowedHosts: ['https://24dc-129-127-202-54.ngrok-free.app/'],
  },
})
