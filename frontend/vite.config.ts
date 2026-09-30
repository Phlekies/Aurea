import react from '@vitejs/plugin-react';
import { defineConfig, loadEnv } from 'vite';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '..', '');
  const target = process.env.API_PROXY_TARGET ?? env.API_PROXY_TARGET ?? 'http://127.0.0.1:8000';
  return {
    plugins: [react()],
    envDir: '..',
    server: { port: 5173, strictPort: true, proxy: { '/health': target, '/api': target } },
  };
});
