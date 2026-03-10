import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // Route /api to Flask backend — it reads tgftp.weather.gov (same source as WU)
      '/api': 'http://localhost:5050',
    },
  },
})
