import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { extname, resolve, sep } from "node:path";

/** Static proof server deliberately adds no COOP or COEP headers. */
export async function startStaticServer() {
  const root = resolve("dist");
  const types = { ".html": "text/html", ".js": "text/javascript", ".wasm": "application/wasm" };
  const httpServer = createServer(async (request, response) => {
    try {
      const pathname = decodeURIComponent(new URL(request.url, "http://127.0.0.1:4387").pathname);
      if (!pathname.startsWith("/proof/")) {
        response.writeHead(404).end();
        return;
      }
      const file = resolve(root, pathname.slice("/proof/".length) || "index.html");
      if (!file.startsWith(root + sep)) {
        response.writeHead(403).end();
        return;
      }
      response.setHeader("Content-Type", types[extname(file)] ?? "application/octet-stream");
      response.end(await readFile(file));
    } catch {
      response.writeHead(404).end();
    }
  });
  await new Promise((resolve, reject) => {
    httpServer.once("error", reject);
    httpServer.listen(4387, "127.0.0.1", resolve);
  });
  return { httpServer };
}
