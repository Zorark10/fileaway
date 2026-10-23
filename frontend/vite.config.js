import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Proxy /upload, /files etc. to the FastAPI backend
    // The frontend App.jsx uses the full API_BASE URL directly,
    // so this proxy is a convenience for development only.
    proxy: {
      '/upload': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/files':  { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
})
