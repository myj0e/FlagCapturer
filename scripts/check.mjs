import { readdir, readFile } from 'node:fs/promises';
import { join, resolve, dirname } from 'node:path';
import { SourceTextModule } from 'node:vm';
import { access } from 'node:fs/promises';
let count = 0;
async function walk(root) {
  for (const entry of await readdir(root, {
    withFileTypes: true
  })) {
    const path = join(root, entry.name);
    if (entry.isDirectory()) await walk(path);else if (/\.(js|mjs)$/.test(path)) {
      try {
        const module = new SourceTextModule(await readFile(path, 'utf8'), {
          identifier: path
        });
        for (const specifier of module.dependencySpecifiers) if (specifier.startsWith('.')) await access(resolve(dirname(path), specifier));
        count++;
      } catch (error) {
        console.error(`${path}: ${error.message}`);
        process.exitCode = 1;
      }
    }
  }
}
for (const root of ['src', 'scripts', 'test', 'assets']) await walk(root);
console.log(`Parsed ${count} modules and checked their relative imports.`);
