// Local documentation renderer. Never connects to the application or cloud data.
import { readdir, readFile, mkdir, mkdtemp, rename, rm, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { spawn } from 'node:child_process';

const root = path.dirname(fileURLToPath(import.meta.url));
const cli = path.join(root, 'node_modules/@mermaid-js/mermaid-cli/src/cli.js');
const inputs = (await readdir(root)).filter(name => name.endsWith('.mmd')).sort();
const temporary = await mkdtemp(path.join(root, '.render-'));
const chrome = process.env.PUPPETEER_EXECUTABLE_PATH;
const browserConfig = path.join(temporary, 'puppeteer.json');
if (chrome) await writeFile(browserConfig, JSON.stringify({ executablePath: chrome, headless: true }));
const run = args => new Promise((resolve, reject) => {
  const child = spawn(process.execPath, [cli, ...args], { stdio: ['ignore', 'pipe', 'pipe'] });
  let diagnostics = '';
  child.stdout.on('data', data => { diagnostics += data; });
  child.stderr.on('data', data => { diagnostics += data; });
  child.on('error', reject);
  child.on('close', code => code === 0 ? resolve() : reject(new Error(diagnostics)));
});
try {
  for (const input of inputs) {
    const name = input.slice(0, -4);
    for (const extension of ['svg', 'png']) {
      const output = path.join(temporary, `${name}.${extension}`);
      const args = ['-i', path.join(root, input), '-o', output, '-c', path.join(root, 'theme.json'),
        '-b', 'white', '--size', '3200', '--no-font-embed'];
      if (extension === 'png') args.push('-s', '1.5');
      if (chrome) args.push('-p', browserConfig);
      await run(args);
      if (extension === 'svg') {
        const text = await readFile(output, 'utf8');
        if (!text.includes('<svg') || !text.includes('viewBox=')) throw new Error(`Invalid SVG: ${input}`);
      }
    }
    process.stdout.write(`Rendered ${input}\n`);
  }
  // Publish previews only after every diagram has parsed and rendered successfully.
  const destination = path.join(root, 'rendered');
  await mkdir(destination, { recursive: true });
  for (const input of inputs) for (const extension of ['svg', 'png']) {
    const file = `${input.slice(0, -4)}.${extension}`;
    await rename(path.join(temporary, file), path.join(destination, file));
  }
  process.stdout.write(`Verified ${inputs.length} diagrams; SVG and PNG previews updated.\n`);
} finally {
  await rm(temporary, { recursive: true, force: true });
}
