import { defineConfig, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { resolve } from 'node:path';

const previewEntry = '/account-preview.html';

// dev server 下把 / 指向隔离预览页，避免误加载真实的 index.html（那会引入 App/store，依赖 window.biodsh）
const previewRoot: Plugin = {
  name: 'preview-root',
  configureServer(server) {
    server.middlewares.use((req, _res, next) => {
      if (req.url === '/' || req.url === '/index.html') req.url = previewEntry;
      next();
    });
  },
};

export default defineConfig({
  root: resolve(__dirname, 'src/renderer'),
  base: './',
  plugins: [react(), tailwindcss(), previewRoot],
  resolve: { alias: { '@': resolve(__dirname, 'src/renderer/src'), '@shared': resolve(__dirname, 'src/shared') } },
  clearScreen: false,
  server: { port: 5180, open: '/' },
  preview: { port: 5180, open: previewEntry },
  build: {
    outDir: resolve(__dirname, 'dist-web'),
    emptyOutDir: true,
    rollupOptions: { input: resolve(__dirname, 'src/renderer/account-preview.html') },
  },
});
