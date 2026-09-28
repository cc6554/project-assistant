import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Tauri 2 开发环境要求：固定端口 + 不自动打开浏览器
export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    watch: {
      ignored: ["**/src-tauri/**"],
    },
  },
});
