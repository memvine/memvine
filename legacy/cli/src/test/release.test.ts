import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { Store } from '../store.js';
import { headCommit, inspectChangesSince } from '../git.js';
import { buildDigest, compileInto } from '../compile.js';

function fixture(t: { after: (fn: () => void) => void }) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'memvine-release-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const git = (...args: string[]) => execFileSync('git', args, { cwd: root, encoding: 'utf8' });
  git('init', '-q'); git('config', 'user.email', 'test@example.com'); git('config', 'user.name', 'Test');
  fs.writeFileSync(path.join(root, 'auth.ts'), 'password\n'); git('add', '.'); git('commit', '-qm', 'initial');
  return { root, git, store: Store.init(root) };
}

test('unverified replacement cannot retire shared knowledge; failed promotion preserves candidate', t => {
  const { store } = fixture(t);
  const old = store.add({ body: 'password login', kind: 'semantic', verified: true });
  const candidate = store.add({ body: 'magic links', kind: 'semantic', local: false, supersedes: old.meta.id });
  assert.equal(store.get(candidate.meta.id)?.local, true);
  assert.equal(store.get(old.meta.id)?.memory.meta.status, 'active');
  const destination = path.join(store.dir, 'memories', candidate.meta.id + '.md');
  fs.mkdirSync(destination); // deterministic atomic-rename failure
  assert.throws(() => store.write({ ...candidate, meta: { ...candidate.meta, verified: true } }, false));
  assert.throws(() => store.validate(candidate.meta.id));
  assert.ok(fs.existsSync(path.join(store.dir, 'local', candidate.meta.id + '.md')));
  assert.equal(store.get(old.meta.id)?.memory.meta.status, 'active');
  fs.rmdirSync(destination);
  const result = store.validate(candidate.meta.id, 'checked code')!;
  assert.equal(result.promoted, true);
  assert.equal(store.get(old.meta.id)?.memory.meta.status, 'superseded');
  assert.equal(result.memory.meta.learned_at, candidate.meta.learned_at);
  assert.ok(result.memory.meta.validated_at);
  assert.equal(result.memory.meta.evidence, 'checked code');
  assert.equal(store.get(candidate.meta.id)?.local, false);
});

test('unknown history still detects dirty/untracked paths; rename preserves Unicode and newlines', t => {
  const { store, root, git } = fixture(t);
  const m = store.add({ body: 'password login', kind: 'semantic', scope: ['auth.ts'], verified: true });
  m.meta.validated_commit = 'f'.repeat(40); store.write(m, false);
  fs.writeFileSync(path.join(root, 'auth.ts'), 'magic\n');
  const unusual = ' spaced\n日本.ts'; fs.writeFileSync(path.join(root, unusual), 'x');
  const scan = inspectChangesSince(m.meta.validated_commit, root);
  assert.equal(scan.unknown, true); assert.ok(scan.files.includes('auth.ts')); assert.ok(scan.files.includes(unusual));
  assert.match(store.recall('password').text, /UNKNOWN/);
  assert.ok(!buildDigest(store, 12000).includes(m.body));
  git('add', 'auth.ts', unusual); git('commit', '-qm', 'change');
  const base = headCommit(root); assert.equal(base.length, 40);
  git('mv', unusual, 'renamed.ts'); git('commit', '-qm', 'rename');
  const renamed = inspectChangesSince(base, root);
  assert.equal(renamed.unknown, false); assert.ok(renamed.files.includes(unusual)); assert.ok(renamed.files.includes('renamed.ts'));
});

test('budgets include metadata and Unicode; query-only misses abstain', t => {
  const { store } = fixture(t);
  store.add({ body: 'password ' + '🙂'.repeat(1000), kind: 'semantic', verified: true });
  for (const budget of [1, 10, 100, 400, 6000]) {
    const text = store.recall('password', undefined, { budgetBytes: budget }).text;
    assert.ok(Buffer.byteLength(text) <= budget); assert.ok(!text.includes('\uFFFD'));
    assert.ok(Buffer.byteLength(buildDigest(store, budget)) <= budget);
  }
  assert.equal(store.recall('astronaut zebras').memories.length, 0);
  assert.throws(() => store.recall('password', undefined, { budgetBytes: Infinity }));
});

test('malformed entries are isolated and diagnosed; IDs and symlink directories cannot escape', t => {
  const { store, root } = fixture(t);
  store.add({ body: 'valid password memory', kind: 'semantic', verified: true });
  fs.writeFileSync(path.join(store.dir, 'memories/mem_bad1.md'), '---\nid: mem_bad1\nscope: 42\n---\nbad');
  assert.equal(store.list().length, 1); assert.ok(store.diagnostics().some(x => x.includes('mem_bad1')));
  assert.throws(() => store.get('../../outside'));
  fs.writeFileSync(path.join(store.dir, 'config.json'), '{"recall_budget_bytes":-2}');
  assert.throws(() => store.config());
  fs.rmSync(path.join(store.dir, 'local'), { recursive: true });
  fs.symlinkSync(root, path.join(store.dir, 'local'));
  assert.throws(() => store.add({ body: 'escape', kind: 'semantic' }), /safe memory directory/);
});

test('compile preserves user content and shared memories survive a clone', t => {
  const { store, root, git } = fixture(t);
  fs.writeFileSync(path.join(root, 'AGENTS.md'), 'Custom instructions\n');
  compileInto(store, 'AGENTS.md'); compileInto(store, 'AGENTS.md');
  const text = fs.readFileSync(path.join(root, 'AGENTS.md'), 'utf8');
  assert.ok(text.startsWith('Custom instructions\n')); assert.equal(text.split('memvine:begin').length, 2);
  const shared = store.add({ body: 'team lesson', kind: 'semantic', verified: true });
  const local = store.add({ body: 'private hunch', kind: 'semantic' });
  git('add', '.'); git('commit', '-qm', 'memory');
  const clone = path.join(root, 'clone'); git('clone', '-q', root, clone);
  const copy = Store.find(clone)!;
  assert.ok(copy.get(shared.meta.id)); assert.equal(copy.get(local.meta.id), null);
});

test('real MCP stdio lifecycle: capture, recall, validate, stale, revise, bounded read', async t => {
  const { store, root, git } = fixture(t);
  const client = new Client({ name: 'release-test', version: '1.0.0' });
  const transport = new StdioClientTransport({ command: process.execPath, args: [fileURLToPath(new URL('../cli.js', import.meta.url)), 'serve'], cwd: root });
  await client.connect(transport);
  try {
    const call = async (name: string, args: Record<string, unknown>) => {
      const result = await client.callTool({ name, arguments: args });
      assert.ok(!result.isError, JSON.stringify(result));
      return (result.content as Array<{ text: string }>).map(c => c.text).join('\n');
    };
    assert.equal((await client.listTools()).tools.length, 6);
    const saved = await call('remember', { body: 'password login', kind: 'semantic', scope: ['auth.ts'], local: false });
    const id = saved.match(/mem_[a-z0-9]+/)![0];
    assert.equal(store.get(id)?.local, true);
    assert.match(await call('recall', { query: 'password' }), /UNVERIFIED/);
    await call('validate', { id, evidence: 'read auth.ts' }); assert.equal(store.get(id)?.local, false);
    fs.writeFileSync(path.join(root, 'auth.ts'), 'magic links\n');
    assert.match(await call('recall', { query: 'password' }), /stale/);
    await call('check_stale', {});
    git('add', 'auth.ts'); git('commit', '-qm', 'magic links');
    await call('revise', { id, body: 'magic link login', status: 'active' });
    assert.match(await call('recall', { query: 'magic' }), /active/);
    assert.equal(store.get(id)?.memory.meta.learned_commit.length, 40);
    const page = await call('read_memory', { id }); assert.match(page, /magic link login/);
    assert.ok(Buffer.byteLength(page) <= store.config().recall_budget_bytes);
    const large = 'magic ' + '🙂'.repeat(400);
    await call('revise', { id, body: large });
    fs.writeFileSync(path.join(store.dir, 'config.json'), JSON.stringify({ ...store.config(), recall_budget_bytes: 400 }));
    let offset = 0; let assembled = '';
    for (let count = 0; count < 30; count++) {
      const text = await call('read_memory', { id, offset });
      assert.ok(Buffer.byteLength(text) <= 400);
      const body = text.slice(text.indexOf('\n') + 1, text.lastIndexOf('\n'));
      assembled += body;
      const next = text.match(/nextOffset=(\d+)/);
      if (!next) break;
      assert.ok(Number(next[1]) > offset); offset = Number(next[1]);
    }
    assert.equal(assembled, large);

  } finally { await client.close(); }
});

test('fresh repository reports unknown and still sees staged paths', t => {
  const { root, git } = fixture(t);
  const fresh = path.join(root, 'fresh'); fs.mkdirSync(fresh);
  execFileSync('git', ['init', '-q'], { cwd: fresh });
  fs.writeFileSync(path.join(fresh, 'new.ts'), 'x');
  execFileSync('git', ['add', 'new.ts'], { cwd: fresh });
  const scan = inspectChangesSince('0000000', fresh);
  assert.equal(scan.unknown, true); assert.ok(scan.files.includes('new.ts'));
});

test('validation preserves learning provenance; confirmation against dirty code holds only for that content', t => {
  const { store, root, git } = fixture(t);
  const m = store.add({ body: 'password login', kind: 'semantic', scope: ['auth.ts'], verified: true });
  fs.writeFileSync(path.join(root, 'auth.ts'), 'changed');
  assert.equal(store.recall('password').memories[0].meta.status, 'stale', 'unconfirmed after an edit');
  store.validate(m.meta.id, 'checked working tree');
  assert.equal(store.recall('password').memories[0].meta.status, 'active', 'confirmed against this exact content');
  fs.writeFileSync(path.join(root, 'auth.ts'), 'changed again');
  assert.equal(store.recall('password').memories[0].meta.status, 'stale', 'content moved on after confirmation');
  git('checkout', '--', 'auth.ts');
  assert.equal(store.recall('password').memories[0].meta.status, 'stale', 'discarding the confirmed edit is also suspect');
  fs.writeFileSync(path.join(root, 'auth.ts'), 'changed');
  git('add', 'auth.ts'); git('commit', '-qm', 'checked change');
  assert.equal(store.recall('password').memories[0].meta.status, 'active', 'committing the confirmed content keeps it fresh');
  const confirmed = store.validate(m.meta.id, 'checked commit')!.memory;
  assert.equal(confirmed.meta.learned_at, m.meta.learned_at);
  assert.equal(confirmed.meta.learned_commit, m.meta.learned_commit);
  assert.equal(confirmed.meta.validated_commit, headCommit(root));
  assert.equal(store.recall('password').memories[0].meta.status, 'active');
});

test('failed predecessor retirement is recoverable without losing the local candidate', t => {
  const { store } = fixture(t);
  const old = store.add({ body: 'old fact', kind: 'semantic', verified: true });
  const candidate = store.add({ body: 'corrected fact', kind: 'semantic', supersedes: old.meta.id });
  const write = store.write.bind(store);
  store.write = (memory, local) => {
    if (memory.meta.id === old.meta.id) throw new Error('simulated disk failure');
    write(memory, local);
  };
  assert.throws(() => store.validate(candidate.meta.id), /simulated disk failure/);
  assert.ok(fs.existsSync(path.join(store.dir, 'local', candidate.meta.id + '.md')));
  assert.equal(store.get(old.meta.id)?.memory.meta.status, 'active');
  store.write = write;
  store.validate(candidate.meta.id);
  assert.equal(store.get(old.meta.id)?.memory.meta.status, 'superseded');
  assert.ok(!fs.existsSync(path.join(store.dir, 'local', candidate.meta.id + '.md')));
});
