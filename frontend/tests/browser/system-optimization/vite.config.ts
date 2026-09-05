import { defineConfig, type Plugin } from "vite";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const fixtureRoot = dirname(fileURLToPath(import.meta.url));
const frontendRoot = resolve(fixtureRoot, "../../..");
const port = 4178;
const headers = {
  "X-Robots-Tag": "noindex, nofollow, noarchive",
  "Cache-Control": "no-store",
  "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; connect-src 'none'; worker-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
};

const isolation: Plugin = {
  name: "synthetic-system-optimization-only",
  configResolved(config) {
    for (const options of [config.server, config.preview]) {
      if (options.host !== "127.0.0.1" || options.port !== port || options.strictPort !== true || options.proxy) {
        throw new Error("Fixture must remain on 127.0.0.1:4178 with strictPort and without proxy");
      }
    }
    const output = resolve(config.root, config.build.outDir);
    if (output !== resolve(fixtureRoot, ".build") && !/^\/private\/tmp\/vkpi-ui-fixture\.[^/]+$/.test(output)) {
      throw new Error("Fixture output must be its own .build or a mktemp /private/tmp/vkpi-ui-fixture.* directory");
    }
  },
  configureServer(server) {
    server.middlewares.use((request, response, next) => {
      if (!["GET", "HEAD"].includes(request.method ?? "") || /^\/(api(?:\/|$)|health(?:\/|$)|graphql(?:\/|$))/.test(request.url ?? "")) {
        response.statusCode = 403;
        response.end("Synthetic fixture: backend requests are disabled");
      } else next();
    });
  },
  configurePreviewServer(server) {
    server.middlewares.use((request, response, next) => {
      if (!["GET", "HEAD"].includes(request.method ?? "") || /^\/(api(?:\/|$)|health(?:\/|$)|graphql(?:\/|$))/.test(request.url ?? "")) {
        response.statusCode = 403;
        response.end("Synthetic fixture: backend requests are disabled");
      } else next();
    });
  },
};

export default defineConfig({
  root: fixtureRoot,
  envDir: fixtureRoot,
  envPrefix: "SYNTHETIC_FIXTURE_PUBLIC_",
  publicDir: false,
  cacheDir: resolve(fixtureRoot, ".vite-cache"),
  plugins: [isolation],
  esbuild: { jsx: "automatic" },
  resolve: {
    alias: [
      { find: /^.*\/services\/vkpi\/actionInbox-api(?:\.ts)?$/, replacement: resolve(fixtureRoot, "mock-actionInbox-api.ts") },
      { find: /^.*\/services\/http(?:\.ts)?$/, replacement: resolve(fixtureRoot, "mock-http.ts") },
      { find: /^.*\/lib\/api(?:\.ts)?$/, replacement: resolve(fixtureRoot, "mock-http.ts") },
    ],
  },
  server: { host: "127.0.0.1", port, strictPort: true, proxy: undefined, hmr: false, headers, allowedHosts: ["127.0.0.1"] },
  preview: { host: "127.0.0.1", port, strictPort: true, proxy: undefined, headers, allowedHosts: ["127.0.0.1"] },
  build: { outDir: resolve(fixtureRoot, ".build"), emptyOutDir: false, sourcemap: false },
});
