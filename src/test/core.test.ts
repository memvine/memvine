/** Core lifecycle tests: init → add → list → supersede → stale → compile. */
import { test } from "node:test";
import assert from "node:assert";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { execFileSync } from "node:child_process";
import { Store } from "../store.js";
import { findStale, markStale } from "../staleness.js";
import { buildDigest, compileInto } from "../compile.js";
import { headCommit } from "../git.js";

function makeRepo(): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "memvine-test-"));
  const g = (args: string[]) => execFileSync("git", args, { cwd: dir });
  g(["init", "-q"]);
  g(["config", "user.email", "test@example.com"]);
  g(["config", "user.name", "Test"]);
  fs.mkdirSync(path.join(dir, "src/auth"), { recursive: true });
  fs.writeFileSync(path.join(dir, "src/auth/login.ts"), "export const a = 1;\n");
  g(["add", "-A"]);
  g(["commit", "-q", "-m", "initial"]);
  return dir;
}

test("init creates store with gitignored local dir", () => {
  const repo = makeRepo();
  const store = Store.init(repo);
  assert.ok(fs.existsSync(path.join(store.dir, "memories")));
  assert.ok(fs.existsSync(path.join(store.dir, "local")));
  assert.match(
    fs.readFileSync(path.join(store.dir, ".gitignore"), "utf8"),
    /local\//,
  );
});

test("add, list, tags, and scope filtering", () => {
  const store = Store.init(makeRepo());
  store.add({
    body: "Auth tests need vault container",
    kind: "semantic",
    tags: ["test", "auth"],
    scope: ["src/auth/**"],
  });
  store.add({ body: "We use pnpm not npm", kind: "semantic" });
  assert.equal(store.list().length, 2);
  const authScoped = store.list({ forPath: "src/auth/login.ts" });
  assert.equal(authScoped.length, 2); // scoped match + repo-wide
  const otherScoped = store.list({ forPath: "src/billing/pay.ts" });
  assert.equal(otherScoped.length, 1); // only repo-wide
  // tags are searchable
  assert.equal(store.search("auth").length >= 1, true);
});

test("recall works when the agent passes an absolute path", () => {
  const repo = makeRepo();
  const store = Store.init(repo);
  store.add({
    body: "Auth uses magic links, chosen over passwords",
    kind: "semantic",
    tags: ["auth"],
    scope: ["src/auth/**"],
  });
  const absolute = path.join(store.root, "src/auth/login.ts");
  // Absolute path must resolve to the same scope match as the repo-relative one.
  assert.equal(store.search("magic links", absolute).length, 1);
  assert.equal(store.search("magic links", "src/auth/login.ts").length, 1);
  // ...and a path outside the scope still excludes the scoped memory.
  assert.equal(store.search("magic links", path.join(store.root, "src/billing/pay.ts")).length, 0);
});

test("recall falls back to scoped memories when the query wording doesn't overlap", () => {
  const store = Store.init(makeRepo());
  store.add({
    body: "Auth uses magic links, chosen over passwords",
    kind: "semantic",
    tags: ["auth"],
    scope: ["src/auth/**"],
  });
  // No lexical overlap with the memory body/tags, but the memory is relevant to
  // the path — recall must surface it rather than coming back empty.
  const hits = store.search("how does sign-in work", "src/auth/login.ts");
  assert.equal(hits.length, 1);
});

test("supersede retires the old memory", () => {
  const store = Store.init(makeRepo());
  const old = store.add({ body: "API uses REST", kind: "semantic" });
  store.add({ body: "API migrated to gRPC", kind: "semantic", supersedes: old.meta.id });
  const retired = store.get(old.meta.id);
  assert.equal(retired?.memory.meta.status, "superseded");
  assert.equal(store.list({ status: ["active"] }).length, 1);
});

test("staleness: semantic memory stales when its code changes; episodic never does", () => {
  const repo = makeRepo();
  const store = Store.init(repo);
  store.add({
    body: "Login flow uses magic links",
    kind: "semantic",
    scope: ["src/auth/**"],
  });
  store.add({
    body: "March 2026: tried passwordless-only rollout, support tickets spiked, partially rolled back",
    kind: "episodic",
    scope: ["src/auth/**"], // same scope — but history never stales
  });
  store.add({ body: "Repo-wide fact", kind: "semantic" });
  assert.equal(findStale(store).length, 0, "fresh right after learning");

  // Change the scoped file in a new commit.
  fs.writeFileSync(path.join(repo, "src/auth/login.ts"), "export const a = 2;\n");
  const g = (args: string[]) => execFileSync("git", args, { cwd: repo });
  g(["add", "-A"]);
  g(["commit", "-q", "-m", "change auth"]);

  const reports = findStale(store);
  assert.equal(reports.length, 1, "only the scoped SEMANTIC memory goes stale");
  assert.equal(reports[0].memory.meta.kind, "semantic");
  assert.deepEqual(reports[0].changedFiles, ["src/auth/login.ts"]);
  assert.equal(markStale(store, reports), 1);
  assert.equal(store.list({ status: ["stale"] }).length, 1);
  assert.equal(findStale(store).length, 0, "idempotent: already-stale not re-reported");
});

test("compile renders digest with markers and respects budget", () => {
  const store = Store.init(makeRepo());
  store.add({ body: "High-value fact", kind: "semantic", tags: ["build"], confidence: "high" });
  const target = compileInto(store, "CLAUDE.md");
  const content = fs.readFileSync(target, "utf8");
  assert.match(content, /memvine:begin/);
  assert.match(content, /High-value fact/);
  assert.match(content, /semantic · build/);
  // Re-compile is idempotent (single block).
  compileInto(store, "CLAUDE.md");
  const again = fs.readFileSync(target, "utf8");
  assert.equal(again.split("memvine:begin").length, 2);
  // Budget: digest never exceeds configured bytes (+small header slack).
  const digest = buildDigest(store, 500);
  assert.ok(Buffer.byteLength(digest, "utf8") < 800);
});

test("recall caps the number of memories returned", () => {
  const store = Store.init(makeRepo());
  for (let i = 0; i < 20; i++) {
    store.add({ body: `alpha fact number ${i}`, kind: "semantic" });
  }
  const r = store.recall("alpha", undefined, { limit: 5 });
  assert.equal(r.memories.length, 5);
  assert.equal(r.omitted, 15);
});

test("recall stops at the byte budget even under the count cap", () => {
  const store = Store.init(makeRepo());
  const big = "beta ".repeat(200); // ~1000 bytes each
  for (let i = 0; i < 10; i++) store.add({ body: big + i, kind: "semantic" });
  const r = store.recall("beta", undefined, { limit: 10, budgetBytes: 2500 });
  assert.ok(r.memories.length >= 1 && r.memories.length <= 3, `got ${r.memories.length}`);
  assert.ok(r.omitted > 0);
});

test("recall always returns the top hit, even if it alone exceeds the budget", () => {
  const store = Store.init(makeRepo());
  store.add({ body: "gamma ".repeat(500), kind: "semantic" });
  const r = store.recall("gamma", undefined, { budgetBytes: 10 });
  assert.equal(r.memories.length, 1);
  assert.equal(r.omitted, 0);
});

test("recall ranks higher-confidence memories above equally-relevant low-confidence ones", () => {
  const store = Store.init(makeRepo());
  store.add({ body: "delta fact", kind: "semantic", confidence: "low" });
  store.add({ body: "delta fact", kind: "semantic", confidence: "high" });
  const ranked = store.search("delta");
  assert.equal(ranked.length, 2);
  assert.equal(ranked[0].meta.confidence, "high", "high-confidence surfaces first");
});

test("recall down-ranks a stale memory below an equally-relevant active one, but still returns it", () => {
  const store = Store.init(makeRepo());
  const fresh = store.add({ body: "epsilon fact", kind: "semantic", confidence: "medium" });
  const going = store.add({ body: "epsilon fact", kind: "semantic", confidence: "medium" });
  // Force one stale.
  const s = store.get(going.meta.id)!;
  s.memory.meta.status = "stale";
  store.write(s.memory, s.local);
  const ranked = store.search("epsilon");
  assert.equal(ranked.length, 2, "stale is down-ranked, not excluded");
  assert.equal(ranked[0].meta.status, "active");
  assert.equal(ranked[0].meta.id, fresh.meta.id);
});

test("add records validated_commit == learned_commit", () => {
  const store = Store.init(makeRepo());
  const m = store.add({ body: "x", kind: "semantic" });
  assert.equal(m.meta.validated_commit, m.meta.learned_commit);
});

test("validated_commit gates staleness: revalidating at HEAD clears the flag", () => {
  const repo = makeRepo();
  const store = Store.init(repo);
  const m = store.add({
    body: "Login uses magic links",
    kind: "semantic",
    scope: ["src/auth/**"],
  });
  const g = (args: string[]) => execFileSync("git", args, { cwd: repo });
  fs.writeFileSync(path.join(repo, "src/auth/login.ts"), "export const a = 2;\n");
  g(["add", "-A"]);
  g(["commit", "-q", "-m", "change auth"]);
  assert.equal(findStale(store).length, 1, "flags once the scoped evidence changes");

  // Simulate what `revise` does: re-confirm the memory at the current HEAD.
  const found = store.get(m.meta.id)!;
  found.memory.meta.validated_commit = headCommit(repo);
  store.write(found.memory, found.local);
  assert.equal(findStale(store).length, 0, "clears once re-confirmed against HEAD");
});

test("older memory files without validated_commit default it to learned_commit", () => {
  const store = Store.init(makeRepo());
  fs.writeFileSync(
    path.join(store.dir, "memories", "mem_legacy1.md"),
    "---\nid: mem_legacy1\nkind: semantic\ntags: []\nscope: []\n" +
      "learned_at: 2026-01-01T00:00:00Z\nlearned_commit: abc1234\nagent: cli\n" +
      "status: active\nconfidence: medium\n---\nlegacy body\n",
  );
  const got = store.get("mem_legacy1")!;
  assert.equal(got.memory.meta.validated_commit, "abc1234");
});

test("compiled digest instructs the agent to recall/remember, even when empty", () => {
  const store = Store.init(makeRepo());
  // No memories yet — the usage protocol must still be present so agents are
  // told to call the tools from the very first session.
  const digest = buildDigest(store, 12_000);
  assert.match(digest, /recall/);
  assert.match(digest, /remember/);
});

test("local memories stay out of the compiled digest", () => {
  const store = Store.init(makeRepo());
  store.add({ body: "My personal note", kind: "episodic", local: true });
  store.add({ body: "Shared team fact", kind: "semantic" });
  const digest = buildDigest(store, 12_000);
  assert.ok(!digest.includes("My personal note"));
  assert.ok(digest.includes("Shared team fact"));
});
