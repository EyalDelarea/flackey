import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { '/api': { target: 'http://127.0.0.1:8765', changeOrigin: true } } },
  build: { outDir: 'dist', emptyOutDir: true },
  // vmThreads builds jsdom once per worker instead of once per test file (35 files), and still isolates
  // each file in its own VM context: about 23s down to 10s locally, all tests unchanged.
  test: { environment: 'jsdom', pool: 'vmThreads', setupFiles: ['./src/setupTests.ts'], globals: true },
})
