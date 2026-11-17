import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { parse } from 'smol-toml';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { configureCodex } from '../codex.js';

function fixture(t: { after: (fn: () => void) => void }) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'memvine codex 日本-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  execFileSync('git', ['init', '-q'], { cwd: root });
  return root;
}

test('init --codex produces a working MCP connection without the codex CLI', async t => {
  const root = fixture(t);
  const cli = fileURLToPath(new URL('../cli.js', import.meta.url));
  const out = execFileSync(process.execPath, [cli, 'init', '--codex'], { cwd: root, encoding: 'utf8' });
  assert.match(out, /Configured Codex/);
  assert.ok(fs.existsSync(path.join(root, 'AGENTS.md')));
  const config = parse(fs.readFileSync(path.join(root, '.codex/config.toml'), 'utf8')) as any;
  const server = config.mcp_servers.memvine;
  assert.equal(fs.realpathSync(server.cwd), fs.realpathSync(root));
  const client = new Client({ name: 'codex-config-test', version: '1.0.0' });
  await client.connect(new StdioClientTransport(server));
  try {
    assert.equal((await client.listTools()).tools.length, 6);
    const result = await client.callTool({ name: 'recall', arguments: { query: 'project' } });
    assert.ok(!result.isError);
  } finally { await client.close(); }
});

test('Codex setup preserves existing settings and is idempotent', t => {
  const root = fixture(t);
  fs.mkdirSync(path.join(root, '.codex'));
  const file = path.join(root, '.codex/config.toml');
  const original = '# Keep this comment\nmodel = "example-model"\n[mcp_servers.other]\ncommand = "other"\n';
  fs.writeFileSync(file, original);
  configureCodex(root);
  const first = fs.readFileSync(file, 'utf8');
  assert.ok(first.startsWith(original));
  assert.equal(configureCodex(root).created, false);
  assert.equal(fs.readFileSync(file, 'utf8'), first);
});

test('Codex setup does not overwrite conflicting, disabled or malformed settings', t => {
  const root = fixture(t);
  fs.mkdirSync(path.join(root, '.codex'));
  const file = path.join(root, '.codex/config.toml');
  for (const original of ['[mcp_servers.memvine]\ncommand = "custom"\n', 'bad = [', 'mcp_servers = "invalid"']) {
    fs.writeFileSync(file, original);
    assert.throws(() => configureCodex(root));
    assert.equal(fs.readFileSync(file, 'utf8'), original);
  }
  fs.rmSync(file);
  configureCodex(root);
  fs.appendFileSync(file, '\nenabled = false\n');
  const disabled = fs.readFileSync(file, 'utf8');
  assert.throws(() => configureCodex(root));
  assert.equal(fs.readFileSync(file, 'utf8'), disabled);
});

test('Codex setup refuses config symlinks', t => {
  const root = fixture(t);
  fs.mkdirSync(path.join(root, '.codex'));
  const target = path.join(root, 'other.toml'); fs.writeFileSync(target, '# original');
  fs.symlinkSync(target, path.join(root, '.codex/config.toml'));
  assert.throws(() => configureCodex(root), /symlink/);
  assert.equal(fs.readFileSync(target, 'utf8'), '# original');
});
