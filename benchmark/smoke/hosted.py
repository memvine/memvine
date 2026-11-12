#!/usr/bin/env python3
"""End-to-end smoke test of a deployed Memvine (API + console), safe to run against production.

    MEMVINE_SMOKE_KEY=mv_mk_... python benchmark/smoke/hosted.py \
        --api https://api.example --console https://app.example [--no-synthesis]

Standard library only, so it runs anywhere with Python 3.9+. The operator or organization key is
read from an environment variable (--management-key-env, default MEMVINE_SMOKE_KEY), never from
the command line, and is never printed; neither is the project key the run creates.

What it does (each step prints PASS/FAIL with detail and time; exit 1 on any failure):
  1. API /health and /v1/auth/config; console /login renders, and shows the sign-up mode it read
     from the API (proves the console reaches the API over its private network address).
  2. Creates a throwaway project `smoke-<random>` and a project key, then
     - synthesis (default): ingests one small episode with `instant: true` holding one durable rule
       and one progress line; exactly the rule must become a memory (category rule, standing), the
       progress must not; a related recall carries the rule in its briefing; an unrelated recall
       abstains; forgetting the memory (PATCH status retired) removes it from recall.
     - --no-synthesis (no model key on the server yet): pauses the project, ingests without
       `instant`, and checks the episode is queued as a job.
     GET /v1/health/synthesis?project=<slug> answers and reports no failed synthesis for the project.
     The project is deleted (?confirm=<slug>) in a finally block, whatever happened, and the run
     checks it is gone and its key no longer works.
  3. Latency per call: p50 and max per endpoint; a warning for any non-synthesis call over 2 s.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import datetime, timezone

SLOW_SECONDS = 2.0

# Invented "Northwind" billing world. One durable rule and one line of progress: only the rule is memory.
RULE_MARKER = "backfill"
PROGRESS_MARKERS = ("12 of 30", "halfway", "export tests")
EPISODE = """Planning chat, Northwind billing

Sam: Status: I'm halfway through migrating the export tests; 12 of 30 pass so far, I'll finish the rest tomorrow.
Priya: One thing for everyone, from now on: never run the invoice backfill script against production without a --dry-run pass first. Last quarter it double-charged forty accounts."""
RELATED_QUESTION = "How should I run the invoice backfill script against production?"
UNRELATED_QUESTION = "What is a good recipe for sourdough bread with a rye starter?"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # surface 3xx to the caller instead of following it
        return None


OPENER = urllib.request.build_opener(NoRedirect)


class Http:
    """A tiny JSON client that times every call."""

    def __init__(self, timeout: float):
        self.timeout = timeout
        self.timings: list[tuple[str, float, bool]] = []  # (label, seconds, synthesis)

    def call(self, method: str, url: str, *, body=None, token: str | None = None, label: str | None = None,
             synthesis: bool = False, timeout: float | None = None) -> tuple[int, object, dict]:
        headers = {"Accept": "application/json", "User-Agent": "memvine-smoke/1"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        start = time.monotonic()
        try:
            with OPENER.open(req, timeout=timeout or self.timeout) as res:
                status, raw, res_headers = res.status, res.read(), dict(res.headers)
        except urllib.error.HTTPError as err:
            status, raw, res_headers = err.code, err.read(), dict(err.headers or {})
        finally:
            self.timings.append((label or f"{method} {urllib.parse.urlsplit(url).path}", time.monotonic() - start, synthesis))
        text = raw.decode("utf-8", "replace")
        try:
            parsed: object = json.loads(text) if text else None
        except json.JSONDecodeError:
            parsed = text
        return status, parsed, res_headers


class Failed(Exception):
    pass


def need(cond, message: str) -> None:
    if not cond:
        raise Failed(message)


def short(value, limit: int = 300) -> str:
    text = value if isinstance(value, str) else json.dumps(value)
    return text if len(text) <= limit else text[:limit] + "…"


class Run:
    def __init__(self):
        self.results: list[tuple[str, bool, str, float]] = []
        self.warnings: list[str] = []

    def step(self, name: str, fn: Callable[[], str | None]) -> bool:
        start = time.monotonic()
        try:
            detail, ok = fn() or "", True
        except Failed as exc:
            detail, ok = str(exc), False
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            detail, ok = f"network error: {exc}", False
        except Exception as exc:  # noqa: BLE001 - a smoke test reports, it does not crash
            detail, ok = f"{type(exc).__name__}: {exc}", False
        took = time.monotonic() - start
        self.results.append((name, ok, detail, took))
        print(f"{'PASS' if ok else 'FAIL'}  {name}  ({took:.2f}s){'  — ' + detail if detail else ''}", flush=True)
        return ok

    def skip(self, name: str, why: str) -> None:
        print(f"SKIP  {name}  — {why}", flush=True)

    def warn(self, message: str) -> None:
        self.warnings.append(message)
        print(f"WARN  {message}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="End-to-end smoke test of a deployed Memvine (safe for production).")
    ap.add_argument("--api", required=True, help="the API's public URL, e.g. https://api.memvine.dev")
    ap.add_argument("--console", help="the console's public URL, e.g. https://app.memvine.dev (console checks skipped without it)")
    ap.add_argument("--management-key-env", default="MEMVINE_SMOKE_KEY",
                    help="environment variable holding the operator or organization key (mv_mk_...); default MEMVINE_SMOKE_KEY")
    ap.add_argument("--no-synthesis", action="store_true",
                    help="the server has no model key yet: check that ingest queues instead of learning from it")
    ap.add_argument("--conversation", action="store_true",
                    help="also check conversation mode in a second throwaway project: a small detail is kept and recalled, "
                         "and a summary question is answered from digests (needs the model key)")
    ap.add_argument("--timeout", type=float, default=30.0, help="seconds per ordinary call (default 30)")
    ap.add_argument("--synthesis-timeout", type=float, default=180.0, help="seconds for the instant ingest (default 180)")
    args = ap.parse_args()

    api = args.api.rstrip("/")
    console = args.console.rstrip("/") if args.console else None
    http = Http(args.timeout)
    run = Run()
    started = time.monotonic()
    print(f"Memvine smoke test · API {api}" + (f" · console {console}" if console else "")
          + (" · no synthesis" if args.no_synthesis else ""), flush=True)

    # ------------------------------------------------------------------ 1. reachability
    state: dict = {}

    def api_health():
        status, body, _ = http.call("GET", f"{api}/health", label="GET /health")
        need(status == 200 and isinstance(body, dict) and body.get("ok") is True, f"HTTP {status}: {short(body)}")
        return f"version {body.get('version')}"

    def auth_config():
        status, body, _ = http.call("GET", f"{api}/v1/auth/config", label="GET /v1/auth/config")
        need(status == 200 and isinstance(body, dict) and body.get("signup"), f"HTTP {status}: {short(body)}")
        state["signup"] = body["signup"]
        return f"signup {body['signup']}"

    def console_login():
        status, body, headers = http.call("GET", f"{console}/login", label="GET console /login")
        if status in (301, 302, 303, 307, 308):
            raise Failed(f"HTTP {status} to {headers.get('Location') or headers.get('location')}: the console runs on mock "
                         "data (MEMVINE_API_URL unset) or holds MEMVINE_MANAGEMENT_KEY")
        need(status == 200 and isinstance(body, str),
             f"HTTP {status}: the console could not render /login; check its logs and MEMVINE_API_URL (an unreachable API fails this page)"
             if isinstance(body, str) and "<html" in body[:500].lower() else f"HTTP {status}: {short(body)}")
        need("Sign in to the Memvine Console" in body, "the page does not look like the console's sign-in page")
        signup = state.get("signup")
        # The footer is drawn from the API's /v1/auth/config, fetched server-side over the private network.
        markers = {"open": "Create an account", "invite": "New accounts need an invite link"}
        if signup in markers:
            need(markers[signup] in body, f"the API says signup={signup!r} but the page does not show {markers[signup]!r}: "
                 "the console could not reach the API (check MEMVINE_API_URL and the private network)")
            return f"rendered signup={signup} from the API"
        if any(m in body for m in markers.values()):
            raise Failed(f"the page shows a sign-up mode the API did not report ({signup!r})")
        run.warn(f"signup={signup!r}: the login page shows no sign-up footer, so it cannot prove the console reached the API")
        return "page rendered (API path unproven)"

    run.step("API /health", api_health)
    run.step("API /v1/auth/config", auth_config)
    if console:
        if "signup" in state:
            run.step("console /login rendered API data", console_login)
        else:
            run.skip("console /login rendered API data", "no /v1/auth/config answer to compare with")
    else:
        run.skip("console /login rendered API data", "no --console given")

    # ------------------------------------------------------------------ 2. a throwaway project
    mkey = os.environ.get(args.management_key_env, "").strip()
    if not mkey:
        run.step("management key", lambda: (_ for _ in ()).throw(Failed(
            f"set {args.management_key_env} to the operator or an organization key (mv_mk_...) to run the project checks")))
    else:
        project_flow(api, http, run, mkey, args)
        if args.conversation and not args.no_synthesis:
            conversation_flow(api, http, run, mkey, args)

    # ------------------------------------------------------------------ 3. latency and summary
    report_latency(http, run)
    failed = [r for r in run.results if not r[1]]
    total = time.monotonic() - started
    print(f"\n{len(run.results) - len(failed)}/{len(run.results)} passed, {len(run.warnings)} warnings, {total:.1f}s total")
    for name, _, detail, _ in failed:
        print(f"  FAILED {name}: {detail}")
    return 1 if failed else 0


def project_flow(api: str, http: Http, run: Run, mkey: str, args) -> None:
    slug = f"smoke-{secrets.token_hex(4)}"
    s: dict = {}  # the run's project key and memory id; never printed

    def pkey() -> str:
        need(s.get("key"), "no project key (an earlier step failed)")
        return s["key"]

    def create_project():
        status, body, _ = http.call("POST", f"{api}/v1/projects", token=mkey, label="POST /v1/projects", body={
            "name": slug, "slug": slug, "tags": ["smoke"],
            "description": "Northwind billing: invoices, exports and backfills. Smoke-test project, deleted at the end of the run."})
        need(status == 201 and isinstance(body, dict) and body.get("slug") == slug, f"HTTP {status}: {short(body)}")
        s["created"] = True
        return slug

    def create_key():
        status, body, _ = http.call("POST", f"{api}/v1/projects/{slug}/keys", token=mkey, label="POST /v1/projects/{slug}/keys",
                                    body={"name": "smoke"})
        need(status == 201 and isinstance(body, dict) and str(body.get("key", "")).startswith("mv_sk_"),
             f"HTTP {status}: {short({k: v for k, v in body.items() if k != 'key'} if isinstance(body, dict) else body)}")
        s["key"] = body["key"]
        return f"key …{body.get('suffix')}"

    episode = {"id": f"smoke-{secrets.token_hex(4)}", "content": EPISODE, "label": "Memvine smoke test",
               "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds")}  # noqa: UP017 - runs on 3.10

    def ingest_instant():
        status, body, _ = http.call("POST", f"{api}/v1/ingest", token=pkey(), label="POST /v1/ingest instant", synthesis=True,
                                    timeout=args.synthesis_timeout, body={"items": [episode], "instant": True})
        need(status in (200, 202) and isinstance(body, dict), f"HTTP {status}: {short(body)}")
        instant = body.get("instant") or {}
        need(instant.get("status") == "done", f"not learned within the request: {instant.get('reason') or short(body)}")
        memories = [m["memory"] for m in instant.get("memories", [])]
        statements = [m["statement"] for m in memories]
        need(len(memories) == 1, f"expected exactly 1 memory, got {len(memories)}: {statements}")
        m = memories[0]
        need(RULE_MARKER in m["statement"].lower(), f"the memory is not the rule: {m['statement']!r}")
        need(m.get("category") == "rule" and m.get("standing") is True,
             f"the rule came out as category={m.get('category')!r} standing={m.get('standing')!r}: {m['statement']!r}")
        s["memory"], s["statement"] = m["id"], m["statement"]
        return f"1 memory, rule + standing: {m['statement']!r}"

    def memories_listing():
        status, body, _ = http.call("GET", f"{api}/v1/memories?limit=50", token=pkey(), label="GET /v1/memories")
        need(status == 200 and isinstance(body, list), f"HTTP {status}: {short(body)}")
        live = [m for m in body if m.get("status") in ("active", "stale")]
        progress = [m["statement"] for m in live if any(p in m["statement"].lower() for p in PROGRESS_MARKERS)]
        need(not progress, f"the progress line became memory: {progress}")
        need([m["id"] for m in live] == [s["memory"]], f"expected only the rule in memory, got {[m['statement'] for m in live]}")
        return "only the rule; the progress line is not memory"

    def recall(query: str, label: str):
        status, body, _ = http.call("POST", f"{api}/v1/recall", token=pkey(), label=label, body={"query": query})
        need(status == 200 and isinstance(body, dict), f"HTTP {status}: {short(body)}")
        return body

    def recall_related():
        need(s.get("statement"), "no memory to recall (the ingest step failed)")
        body = recall(RELATED_QUESTION, "POST /v1/recall")
        need(not body.get("abstained"), f"abstained ({body.get('abstain_reason')}, relevance {body.get('top_relevance')})")
        need(s["statement"] in body.get("briefing", ""), f"the rule is not in the briefing: {short(body.get('briefing'))}")
        return f"rule in the briefing (relevance {body.get('top_relevance')}, {body.get('tokens')} tokens)"

    def recall_unrelated():
        # Abstaining on an empty project proves nothing: this needs the rule in memory to pass over.
        need(s.get("memory"), "no memory to abstain over (the ingest step failed)")
        body = recall(UNRELATED_QUESTION, "POST /v1/recall")
        need(body.get("abstained") is True, f"did not abstain (relevance {body.get('top_relevance')}): {short(body.get('briefing'))}")
        return f"abstained: {body.get('abstain_reason')} (relevance {body.get('top_relevance')}, threshold {body.get('min_relevance')})"

    def forget():
        need(s.get("memory"), "no memory to forget (the ingest step failed)")
        status, body, _ = http.call("PATCH", f"{api}/v1/memories/{s['memory']}", token=pkey(), label="PATCH /v1/memories/{id}",
                                    body={"status": "retired"})
        need(status == 200 and isinstance(body, dict) and body.get("status") == "retired", f"HTTP {status}: {short(body)}")
        after = recall(RELATED_QUESTION, "POST /v1/recall")
        served = [after.get("briefing", ""), *after.get("standing", []), *(i["memory"]["statement"] for i in after.get("items", []))]
        need(not any(s["statement"] in text for text in served), "recall still serves the forgotten rule")
        return f"retired; recall no longer serves it ({'abstained' if after.get('abstained') else 'answered without it'})"

    def pause():
        status, body, _ = http.call("PATCH", f"{api}/v1/projects/{slug}", token=mkey, label="PATCH /v1/projects/{slug}",
                                    body={"processing_paused": True})
        need(status == 200 and isinstance(body, dict), f"HTTP {status}: {short(body)}")
        return "processing paused, so no model call runs for this project"

    def ingest_queued():
        status, body, _ = http.call("POST", f"{api}/v1/ingest", token=pkey(), label="POST /v1/ingest", body={"items": [episode]})
        need(status == 202 and isinstance(body, dict), f"HTTP {status}: {short(body)}")
        need(body.get("queued") == 1 and body.get("items") == [{"id": episode["id"], "outcome": "queued"}], f"unexpected: {short(body)}")
        status, jobs, _ = http.call("GET", f"{api}/v1/jobs", token=pkey(), label="GET /v1/jobs")
        need(status == 200 and isinstance(jobs, dict), f"/v1/jobs HTTP {status}: {short(jobs)}")
        mine = [j for j in jobs.get("jobs", []) if j.get("episode_id") == episode["id"]]
        need(mine, f"no job for the episode: {short(jobs)}")
        return f"queued; job {mine[0]['id']} is {mine[0]['status']}"

    def synthesis_health():
        status, body, _ = http.call("GET", f"{api}/v1/health/synthesis?project={slug}", token=mkey, label="GET /v1/health/synthesis")
        need(status == 200 and isinstance(body, dict) and body.get("scope") == "project", f"HTTP {status}: {short(body)}")
        need(not body.get("failed_count"), f"{body.get('failed_count')} failed synthesis for this project: {short(body.get('issues'))}")
        for issue in body.get("issues", []):
            run.warn(f"synthesis health ({issue.get('condition')}): {issue.get('message')}")
        return "healthy" if body.get("healthy") else f"no failures; {len(body.get('issues', []))} other issue(s), see warnings"

    try:
        if run.step(f"create project {slug}", create_project) and run.step("create project key", create_key):
            if args.no_synthesis:
                run.step("pause synthesis for the project", pause)
                run.step("ingest queues (no synthesis)", ingest_queued)
            else:
                if run.step("instant ingest: the rule becomes memory, the progress does not", ingest_instant):
                    run.step("memory holds only the rule", memories_listing)
                run.step("recall a related question: rule in the briefing", recall_related)
                run.step("recall an unrelated question: abstains", recall_unrelated)
                run.step("forget the memory: recall no longer serves it", forget)
            run.step("synthesis health for the project", synthesis_health)
    finally:
        if s.get("created"):
            cleanup(api, http, run, mkey, slug, s.get("key"))


CONVERSATION = """## Priya · 2026-11-03T09:00:00Z
I ran the dry run of the invoice backfill for Northwind this morning. It took 47 minutes and touched 18,204 invoices. \
I'd like the result as a CSV file, not a PDF.

## Assistant
Noted: 47 minutes for 18,204 invoices. I'll write the result to backfill-report.csv. Do you want the 312 invoices \
that failed validation in a separate file?

## Priya
Yes, put the 312 failures in failures.csv. We'll run the real backfill on Thursday at 06:00 UTC.
"""
DETAIL_QUESTION = "How many minutes did the dry run of the invoice backfill take?"
SUMMARY_QUESTION = "Can you give me a summary of what we discussed about the invoice backfill?"


def conversation_flow(api: str, http: Http, run: Run, mkey: str, args) -> None:
    """Conversation mode in its own throwaway project: every concrete detail is kept (the coding-mode
    flow above proves the opposite, that progress is dropped), a detail question is answered with the
    number, and a summary question gets digests of the conversation."""
    slug = f"smoke-conv-{secrets.token_hex(4)}"
    s: dict = {}

    def create_project():
        status, body, _ = http.call("POST", f"{api}/v1/projects", token=mkey, label="POST /v1/projects", body={
            "name": slug, "slug": slug, "tags": ["smoke"], "memory_mode": "conversation",
            "description": "Smoke-test project for conversation mode, deleted at the end of the run."})
        need(status == 201 and isinstance(body, dict) and body.get("memory_mode") == "conversation", f"HTTP {status}: {short(body)}")
        s["created"] = True
        status, body, _ = http.call("POST", f"{api}/v1/projects/{slug}/keys", token=mkey, label="POST /v1/projects/{slug}/keys",
                                    body={"name": "smoke"})
        need(status == 201 and isinstance(body, dict) and str(body.get("key", "")).startswith("mv_sk_"), f"key: HTTP {status}")
        s["key"] = body["key"]
        return slug

    def ingest():
        episode = {"id": f"smoke-{secrets.token_hex(4)}", "content": CONVERSATION, "label": "Memvine smoke test (conversation)",
                   "timestamp": "2026-11-03T09:05:00+00:00"}
        status, body, _ = http.call("POST", f"{api}/v1/ingest", token=s["key"], label="POST /v1/ingest instant", synthesis=True,
                                    timeout=args.synthesis_timeout, body={"items": [episode], "instant": True})
        need(status in (200, 202) and isinstance(body, dict), f"HTTP {status}: {short(body)}")
        instant = body.get("instant") or {}
        need(instant.get("status") == "done", f"not learned within the request: {instant.get('reason') or short(body)}")
        statements = [m["memory"]["statement"] for m in instant.get("memories", [])]
        need(len(statements) >= 3, f"conversation mode kept only {len(statements)} detail(s): {statements}")
        need(any("47" in t for t in statements), f"the 47 minutes is not among the kept details: {statements}")
        return f"{len(statements)} details kept, the 47 minutes among them"

    def recall(query: str) -> dict:
        status, body, _ = http.call("POST", f"{api}/v1/recall", token=s["key"], label="POST /v1/recall", body={"query": query})
        need(status == 200 and isinstance(body, dict), f"HTTP {status}: {short(body)}")
        need(not body.get("abstained"), f"abstained ({body.get('abstain_reason')})")
        return body

    def recall_detail():
        body = recall(DETAIL_QUESTION)
        need("47" in body.get("briefing", ""), f"the 47 minutes is not in the briefing: {short(body.get('briefing'))}")
        need(body.get("passages"), "no source excerpts beside the details")
        return f"47 minutes in the briefing, with {len(body['passages'])} source excerpt(s)"

    def recall_summary():
        body = recall(SUMMARY_QUESTION)
        digests = body.get("digests") or []
        need(digests, f"a summary question got no digests: {short(body.get('briefing'))}")
        return f"{len(digests)} digest(s): {short(digests[0].get('content'), 120)!r}"

    try:
        if run.step(f"conversation mode: create project {slug}", create_project):
            if run.step("conversation mode: every detail of a short conversation is kept", ingest):
                run.step("conversation mode: a detail question is answered with the number", recall_detail)
                run.step("conversation mode: a summary question gets digests", recall_summary)
    finally:
        if s.get("created"):
            cleanup(api, http, run, mkey, slug, s.get("key"))


def cleanup(api: str, http: Http, run: Run, mkey: str, slug: str, key: str | None) -> None:
    def delete():
        status, body, _ = http.call("DELETE", f"{api}/v1/projects/{slug}?confirm={slug}", token=mkey, label="DELETE /v1/projects/{slug}")
        need(status == 204, f"HTTP {status}: {short(body)} — delete it by hand: DELETE /v1/projects/{slug}?confirm={slug}")
        status, body, _ = http.call("GET", f"{api}/v1/projects", token=mkey, label="GET /v1/projects")
        need(status == 200 and isinstance(body, list), f"listing HTTP {status}: {short(body)}")
        need(slug not in [p.get("slug") for p in body], "still listed after delete")
        if key:
            status, body, _ = http.call("POST", f"{api}/v1/recall", token=key, label="POST /v1/recall", body={"query": "anything"})
            need(status in (401, 403), f"the deleted project's key still answers recall: HTTP {status}")
        return "deleted; not listed; its key is refused"

    run.step(f"delete project {slug}", delete)


def report_latency(http: Http, run: Run) -> None:
    if not http.timings:
        return
    by: dict[str, list[float]] = {}
    for label, seconds, _ in http.timings:
        by.setdefault(label, []).append(seconds)
    print("\nLatency per call (seconds):")
    width = max(len(label) for label in by)
    for label, values in by.items():
        print(f"  {label:<{width}}  n={len(values):<2}  p50 {statistics.median(values):6.3f}  max {max(values):6.3f}")
    plain = [t for _, t, synthesis in http.timings if not synthesis]
    if plain:
        print(f"  {'all non-synthesis calls':<{width}}  n={len(plain):<2}  p50 {statistics.median(plain):6.3f}  max {max(plain):6.3f}")
    for label, seconds, synthesis in http.timings:
        if not synthesis and seconds > SLOW_SECONDS:
            run.warn(f"slow call: {label} took {seconds:.2f}s (budget {SLOW_SECONDS:.0f}s)")


if __name__ == "__main__":
    sys.exit(main())
