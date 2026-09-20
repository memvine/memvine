// Pack the current checkout, inspect its allowlist, and test a production-only install.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';
const source = fileURLToPath(new URL('../', import.meta.url));
const root = fs.mkdtempSync(path.join(os.tmpdir(), 'memvine-package-'));
const run = (command, args, cwd = root) => execFileSync(command, args, { cwd, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
try {
  // --silent keeps lifecycle chatter out of the JSON output; prepack still runs.
  const [packed] = JSON.parse(run('npm', ['pack', '--json', '--silent', '--pack-destination', root], source));
  const names = packed.files.map(f => f.path);
  for (const required of ['dist/cli.js', 'dist/index.js', 'dist/index.d.ts', 'LICENSE', 'README.md', 'docs/architecture.svg']) assert.ok(names.includes(required), required);
  assert.ok(!names.some(p => /(^|\/)(test|node_modules|\.memvine)(\/|$)/.test(p) || p.endsWith('.map')));
  fs.writeFileSync(path.join(root, 'package.json'), '{"private":true}');
  run('npm', ['install', '--omit=dev', '--ignore-scripts', '--no-audit', '--no-fund', '--prefer-offline', path.join(root, packed.filename)]);
  const cli = path.join(root, 'node_modules/memvine/dist/cli.js');
  assert.match(run(process.execPath, [cli, '--version']), /0\.2\.1/);
  assert.match(run(process.execPath, [cli, '--help']), /doctor/);
  const repo = path.join(root, 'repo'); fs.mkdirSync(repo);
  run('git', ['init', '-q'], repo);
  run('git', ['-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '--allow-empty', '-qm', 'initial'], repo);
  run(process.execPath, [cli, 'init', '--codex'], repo);
  assert.ok(fs.existsSync(path.join(repo, '.codex/config.toml')));
  const saved = run(process.execPath, [cli, 'add', 'smoke test durable lesson'], repo);
  const id = saved.match(/mem_[a-z0-9]+/)[0];
  run(process.execPath, [cli, 'validate', id, '--evidence', 'packed smoke test'], repo);
  assert.match(run(process.execPath, [cli, 'list'], repo), /smoke test durable lesson/);
  assert.match(run(process.execPath, [cli, 'doctor'], repo), /not committed/);
  const { Client } = await import(pathToFileURL(path.join(root, 'node_modules/@modelcontextprotocol/sdk/dist/esm/client/index.js')));
  const { StdioClientTransport } = await import(pathToFileURL(path.join(root, 'node_modules/@modelcontextprotocol/sdk/dist/esm/client/stdio.js')));
  const client = new Client({ name: 'packed-smoke', version: '1.0.0' });
  await client.connect(new StdioClientTransport({ command: process.execPath, args: [cli, 'serve'], cwd: repo }));
  try {
    assert.equal((await client.listTools()).tools.length, 6);
    const result = await client.callTool({ name: 'recall', arguments: { query: 'durable lesson' } });
    assert.ok(!result.isError); assert.match(result.content[0].text, /smoke test durable lesson/);
  } finally { await client.close(); }
  console.log(`Packed install passed: ${packed.filename}, ${names.length} files; CLI + MCP, production dependencies only.`);
} finally { fs.rmSync(root, { recursive: true, force: true }); }
