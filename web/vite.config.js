import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  // GH Pages pod ścieżką /mecloneme-mini-deploy/
  base: "/mecloneme-mini-deploy/",
});
