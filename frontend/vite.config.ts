import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 개발 중(npm run dev)에는 /api 를 FastAPI(8787)로 프록시
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": "http://localhost:8787" } },
  build: { outDir: "dist", chunkSizeWarningLimit: 1200 },
});
