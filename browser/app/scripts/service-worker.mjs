import { createHash } from 'node:crypto';
import { readdir, readFile, writeFile } from 'node:fs/promises';
import { join, relative } from 'node:path';

export async function generateWorker(directory) {
  async function walk(path) {
    const files = [];
    for (const entry of await readdir(path, { withFileTypes: true })) {
      const file = join(path, entry.name);
      if (entry.isDirectory()) files.push(...await walk(file));
      else if (entry.name !== 'sw.js') files.push(file);
    }
    return files.sort();
  }
  const paths = await walk(directory);
  const template = await readFile(new URL('./worker-template.js', import.meta.url), 'utf8');
  const hash = createHash('sha256').update(template);
  for (const path of paths) hash.update(relative(directory, path)).update(await readFile(path));
  const worker = template.replace('__VERSION__', hash.digest('hex').slice(0, 20))
    .replace('__ASSETS__', JSON.stringify(paths.map(path => relative(directory, path))));
  await writeFile(join(directory, 'sw.js'), worker);
}
