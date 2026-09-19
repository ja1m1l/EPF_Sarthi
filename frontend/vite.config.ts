import { fileURLToPath, URL } from 'node:url'

import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  define: {
    // amazon-cognito-identity-js reaches for the Node `global` object via its
    // bundled buffer dependency, which does not exist in a browser.
    global: 'globalThis',
  },
})
