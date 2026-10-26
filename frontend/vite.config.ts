/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./src/testSetup.ts"],
    globals: true,
    // Each jsdom worker is CPU-heavy (environment + module transform). One per
    // logical core oversubscribed the machine: ~770s of environment setup
    // squeezed into a ~175s run, and tests started hitting their 5s timeout.
    // Half the cores keeps the run as fast and the timings stable.
    maxWorkers: "50%",
  },
});
