import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import { productionCspPlugin } from './src/build/csp'

export default defineConfig({ plugins: [react(), productionCspPlugin()], server: { host: '127.0.0.1', fs: { allow: ['.', '../shared'] }, proxy: { '/api': 'http://127.0.0.1:8000' } }, test: { environment: 'node', include: ['src/**/*.test.ts', 'src/**/*.test.tsx'] } })
