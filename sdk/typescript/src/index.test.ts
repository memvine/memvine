import assert from "node:assert/strict";
import { test } from "node:test";
import { Memvine, MemvineError } from "./index.js";

const URL_ = process.env.MEMVINE_TEST_URL, KEY = process.env.MEMVINE_TEST_KEY;

test("round trip against a live server", { skip: !(URL_ && KEY) }, async () => {
  const mv = new Memvine({ apiKey: KEY!, baseUrl: URL_ });
  const r1 = await mv.ingest([{ id: "ts-1", content: "## Developer\nWe deploy on Fridays.", timestamp: new Date("2026-10-02T09:00:00Z") }]);
  assert.equal(r1.items[0].id, "ts-1");
  const created = await mv.remember("Never push directly to main.", { kind: "preference" });
  assert.equal(created.memory.standing, true);
  const r = await mv.recall("how should I push code?");
  assert.ok(r.standing.some((s) => s.includes("Never push directly to main")));
  const s = mv.session("ts-session", { agent: "ts-test" });
  await s.send("user", "Use Redis for caching.");
  await s.send("assistant", "Redis it is.");
  assert.equal((await s.state()).turns, 2);
  assert.ok((await s.brief()).text.startsWith("=== memvine"));
  assert.deepEqual((await s.close()).sealed, [[1, 2]]);
  await assert.rejects(new Memvine({ apiKey: "mv_sk_nope", baseUrl: URL_ }).recall("x"), (e: unknown) => e instanceof MemvineError && e.code === "unknown-api-key");
});

test("recall asks for edges; related and graph call their routes", async () => {
  const calls: { url: string; body: unknown }[] = [];
  const fake = (async (url: URL, init: RequestInit) => {
    calls.push({ url: String(url), body: init.body ? JSON.parse(String(init.body)) : undefined });
    return new Response(JSON.stringify({ items: [], edges: [], nodes: [] }), { status: 200 });
  }) as unknown as typeof fetch;
  const mv = new Memvine({ apiKey: "mv_sk_x", baseUrl: "http://memvine.test", fetch: fake });
  await mv.recall("When is the launch?", { include: { related: true } });
  await mv.recall("When is the launch?");
  await mv.related("m-1");
  await mv.graph({ since: new Date("2026-10-19T00:00:00Z"), thread: "t-1" });
  assert.deepEqual((calls[0].body as { include: unknown }).include, { related: true });
  assert.equal((calls[1].body as { include?: unknown }).include, undefined);
  assert.equal(calls[2].url, "http://memvine.test/v1/memories/m-1/related");
  assert.equal(calls[3].url, "http://memvine.test/v1/graph?since=2026-10-19T00%3A00%3A00.000Z&thread=t-1");
  await mv.graph({ all: true });
  assert.equal(calls[4].url, "http://memvine.test/v1/graph?all=true");
});

test("a correction names what it supersedes and leaves the rest to be inherited", async () => {
  const bodies: unknown[] = [];
  const fake = (async (_url: URL, init: RequestInit) => {
    bodies.push(JSON.parse(String(init.body)));
    return new Response(JSON.stringify({ memory: { id: "m-2" }, relation: "update", related: "m-1" }), { status: 200 });
  }) as unknown as typeof fetch;
  const mv = new Memvine({ apiKey: "mv_sk_x", baseUrl: "http://memvine.test", fetch: fake });
  await mv.remember("Launch is 12 Nov.", { supersedes: "m-1" });
  await mv.remember("Deploys on Tuesdays.");
  assert.deepEqual(bodies[0], { statement: "Launch is 12 Nov.", assertion: "stated", supersedes: "m-1" });
  assert.deepEqual(bodies[1], { statement: "Deploys on Tuesdays.", kind: "semantic", subjects: [], assertion: "stated", audience: "project" });
});

test("history and the events feed call their routes", async () => {
  const urls: string[] = [];
  const fake = (async (url: URL) => {
    urls.push(String(url));
    return new Response(JSON.stringify(urls.length === 1 ? [{ id: 1, kind: "created" }] : { events: [], next_cursor: null }), { status: 200 });
  }) as unknown as typeof fetch;
  const mv = new Memvine({ apiKey: "mv_sk_x", baseUrl: "http://memvine.test", fetch: fake });
  const history = await mv.history("m-1");
  assert.equal(history[0].kind, "created");
  await mv.events({ since: new Date("2026-10-19T00:00:00Z"), kind: ["created", "forgotten"], memoryId: "m-1", limit: 20, cursor: "c1" });
  await mv.events({ kind: "stale" });
  await mv.events();
  assert.equal(urls[0], "http://memvine.test/v1/memories/m-1/history");
  assert.equal(urls[1], "http://memvine.test/v1/events?since=2026-10-19T00%3A00%3A00.000Z&kind=created&kind=forgotten&memory_id=m-1&limit=20&cursor=c1");
  assert.equal(urls[2], "http://memvine.test/v1/events?kind=stale");
  assert.equal(urls[3], "http://memvine.test/v1/events");
});

test("refuses to construct without a key", () => {
  assert.throws(() => new Memvine({ apiKey: "" }));
});
