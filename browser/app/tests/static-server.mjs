import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { extname, resolve, sep } from 'node:path';
/** The release test deliberately serves without COOP/COEP, like GitHub Pages. */
export async function startStaticServer(directory, base = '/shared-workspace/') {
  const root = resolve(directory); const paths = [];
  const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.wasm': 'application/wasm', '.png': 'image/png', '.svg': 'image/svg+xml', '.webmanifest': 'application/manifest+json' };
  const server = createServer(async (request, response) => {
    try {
      const url = new URL(request.url, 'http://127.0.0.1'); paths.push(url.pathname);
      if (!url.pathname.startsWith(base)) { response.writeHead(404).end(); return; }
      const file = resolve(root, decodeURIComponent(url.pathname.slice(base.length)) || 'index.html');
      if (!file.startsWith(root + sep)) { response.writeHead(403).end(); return; }
      response.setHeader('Content-Type', types[extname(file)] ?? 'application/octet-stream');
      response.setHeader('Cache-Control', 'no-cache');
      response.end(await readFile(file));
    } catch { response.writeHead(404).end(); }
  });
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  const url = `http://127.0.0.1:${server.address().port}${base}`;
  return { url, paths, close: () => new Promise(resolve => server.close(resolve)) };
}
