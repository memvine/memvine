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

test("local memories stay out of the compiled digest", () => {
  const store = Store.init(makeRepo());
  store.add({ body: "My personal note", kind: "episodic", local: true });
  store.add({ body: "Shared team fact", kind: "semantic" });
  const digest = buildDigest(store, 12_000);
  assert.ok(!digest.includes("My personal note"));
  assert.ok(digest.includes("Shared team fact"));
});
