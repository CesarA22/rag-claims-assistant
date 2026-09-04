/// <reference types="vitest" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The API is same-origin through this proxy, which is why app/ has no CORS
// middleware. Opening the API to an origin list so a dev server can reach it is
// a permanent, deployment-shaped surface bought to solve a development-time
// problem, and this codebase's whole argument is exposure discipline.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test/setup.ts',
  },
})
