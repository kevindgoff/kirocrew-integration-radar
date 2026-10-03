import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Library build loaded by the Crew dashboard host. React, lucide-react and the app
// SDK come from the host's import map, so they must never be bundled.
export default defineConfig({
  plugins: [react()],
  build: {
    lib: {
      entry: 'src/index.tsx',
      formats: ['es'],
      fileName: () => 'index.mjs',
    },
    outDir: 'dist',
    emptyOutDir: true,
    rollupOptions: {
      external: ['react', 'react-dom', 'react/jsx-runtime', 'lucide-react', /^@kirocrew\/app-sdk/],
    },
  },
})
