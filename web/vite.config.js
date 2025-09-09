import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'
export default defineConfig({
  base: '/mecloneme-mini-deploy/',
  plugins: [
    react(),
    VitePWA({
      registerType: 'autoUpdate',
      manifest: {
        name: 'MeCloneMe',
        short_name: 'MCM',
        start_url: '/mecloneme-mini-deploy/',
        display: 'standalone',
        background_color: '#0b0f17',
        theme_color: '#0b0f17',
        icons: [
          { src: 'data:image/svg+xml;utf8,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%22512%22 height=%22512%22><rect width=%22100%25%22 height=%22100%25%22 fill=%22%230b0f17%22/><text x=%2250%25%22 y=%2256%25%22 font-size=%22320%22 text-anchor=%22middle%22 fill=%22%23ffffff%22 font-family=%22Arial%2C%20sans-serif%22>M</text></svg>', sizes: '512x512', type: 'image/svg+xml' },
          { src: 'data:image/svg+xml;utf8,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%22192%22 height=%22192%22><rect width=%22100%25%22 height=%22100%25%22 fill=%22%230b0f17%22/><text x=%2250%25%22 y=%2258%25%22 font-size=%22120%22 text-anchor=%22middle%22 fill=%22%23ffffff%22 font-family=%22Arial%2C%20sans-serif%22>M</text></svg>', sizes: '192x192', type: 'image/svg+xml' }
        ]
      }
    })
  ]
})
