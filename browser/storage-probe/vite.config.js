import { defineConfig } from "vite";
import { readFileSync } from "node:fs";

const isolationWorker = readFileSync(new URL("./node_modules/coi-serviceworker/coi-serviceworker.js", import.meta.url), "utf8");

const headers = {
  "Cross-Origin-Opener-Policy": "same-origin",
  "Cross-Origin-Embedder-Policy": "require-corp",
};

export default defineConfig({
  base: "./",
  plugins: [{
    name: "copy-isolation-worker",
    generateBundle() {
      this.emitFile({ type: "asset", fileName: "coi-serviceworker.js", source: isolationWorker });
    },
    configureServer(server) {
      server.middlewares.use((request, response, next) => {
        if (request.url === "/coi-serviceworker.js") {
          response.setHeader("Content-Type", "text/javascript");
          response.end(isolationWorker);
        } else next();
      });
    },
  }],
  server: { host: "127.0.0.1", port: 4387, strictPort: true, headers },
  preview: { host: "127.0.0.1", port: 4387, strictPort: true, headers },
  optimizeDeps: { exclude: ["@tursodatabase/database-wasm"] },
  build: { target: "esnext" },
});
