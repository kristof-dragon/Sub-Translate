import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// During `npm run dev` we proxy /api to the FastAPI container.
// In production the same-origin rewrite is done by the nginx container.
export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
})
