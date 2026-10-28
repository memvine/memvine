import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { execFileSync } from "node:child_process";
import { Store } from "../store.js";
import { buildDigest, compileInto } from "../compile.js";
import { headCommit } from "../git.js";

for (const change of ["committed", "staged", "unstaged"] as const) {
  test(`recall and compilation derive freshness for ${change} changes without writes`, (t) => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "memvine-freshness-"));
    t.after(() => fs.rmSync(root, { recursive: true, force: true }));
    const git = (...args: string[]) => execFileSync("git", args, { cwd: root, encoding: "utf8" });
    git("init", "-q");
    git("config", "user.email", "test@example.com");
    git("config", "user.name", "Test");
    fs.mkdirSync(path.join(root, "src"));
    const code = path.join(root, "src/auth.ts");
    fs.writeFileSync(code, "export const provider = 'password';\n");
    git("add", ".");
    git("commit", "-qm", "initial");
    const store = Store.init(root);
    const fact = store.add({ body: "Authentication uses password login.", kind: "semantic", scope: ["src/auth.ts"], verified: true });
    const procedure = store.add({ body: "Authenticate with a password.", kind: "procedural", scope: ["src/auth.ts"], verified: true });
    const history = store.add({ body: "Authentication originally used passwords.", kind: "episodic", scope: ["src/auth.ts"], verified: true });
    const unrelated = store.add({ body: "Billing requires a customer ID.", kind: "semantic", scope: ["src/billing.ts"], verified: true });
    const global = store.add({ body: "Use concise comments.", kind: "semantic", verified: true });
    const target = compileInto(store, "AGENTS.md");
    git("add", ".");
    git("commit", "-qm", "memories and digest");
    const bytes = fs.readFileSync(path.join(store.dir, "memories", `${fact.meta.id}.md`), "utf8");
    assert.equal(store.recall("authentication login", "src/auth.ts").memories.find(m => m.meta.id === fact.meta.id)?.meta.status, "active");

    fs.writeFileSync(code, "export const provider = 'magic-link';\n");
    if (change !== "unstaged") git("add", "src/auth.ts");
    if (change === "committed") git("commit", "-qm", "change authentication");
    const statusBefore = git("status", "--porcelain");
    const recalled = store.recall("authentication login", "src/auth.ts");
    assert.equal(recalled.memories.find(m => m.meta.id === fact.meta.id)?.meta.status, "stale");
    assert.equal(store.search("authenticate password", "src/auth.ts").find(m => m.meta.id === procedure.meta.id)?.meta.status, "stale");
    assert.equal(recalled.memories.find(m => m.meta.id === history.meta.id)?.meta.status, "active");
    const digest = buildDigest(store, 12000);
    assert.ok(!digest.includes(fact.body));
    assert.ok(!digest.includes(procedure.body));
    for (const m of [history, unrelated, global]) assert.ok(digest.includes(m.body));
    assert.equal(git("status", "--porcelain"), statusBefore, "read paths do not change files");
    assert.equal(fs.readFileSync(path.join(store.dir, "memories", `${fact.meta.id}.md`), "utf8"), bytes);
    assert.equal(store.get(fact.meta.id)?.memory.meta.status, "active", "persisted status remains unchanged");
    compileInto(store, "AGENTS.md");
    assert.ok(!fs.readFileSync(target, "utf8").includes(fact.body));

    if (change === "committed") {
      const confirmed = store.get(fact.meta.id)!;
      confirmed.memory.body = "Authentication uses magic links.";
      confirmed.memory.meta.validated_commit = headCommit(root);
      store.write(confirmed.memory, false);
      assert.equal(store.recall("authentication", "src/auth.ts").memories.find(m => m.meta.id === fact.meta.id)?.meta.status, "active");
      assert.ok(buildDigest(store, 12000).includes(confirmed.memory.body));
    } else {
      git("restore", "--staged", "--worktree", "src/auth.ts");
      assert.equal(store.recall("authentication", "src/auth.ts").memories.find(m => m.meta.id === fact.meta.id)?.meta.status, "active", "reverted dirty changes are fresh again");
    }
  });
}
