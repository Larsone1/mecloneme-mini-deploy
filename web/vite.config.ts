import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  base: '/mecloneme-mini-deploy/',
  plugins: [react()],
  build: { outDir: 'dist', sourcemap: true },
})
