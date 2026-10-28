"""Section 7 of the memory check (--consolidation): background consolidation on the real fast model.

A throwaway project gets memories written straight into the database, as if write-time reconcile had
missed them: two restatements of one rule in different words, a fact and its later changed value
from a separate source (left unlinked), and a description of code that fails the three tests, beside
six unrelated memories a person kept (a project needs ten active memories to be consolidated). One
run through the API and the worker must merge the restatements, link (or flag) the outdated value,
flag the code description as no longer passing, and delete nothing.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

PROJECT = "tidy"
RULE_OLD = ("Never run database migrations against the shared Northwind staging database from a laptop; "
            "use the CI migrate job.")
RULE_NEW = "Migrations on Northwind's shared staging database must go through the CI migrate job, never from someone's laptop."
FACT_OLD = "The Northwind invoice export job runs nightly at 02:00 UTC."
FACT_NEW = "The Northwind invoice export job now runs at 04:00 UTC, moved because the bank's file window changed."
CODE = ("The InvoiceRenderer class in billing/render.py builds the PDF line items from the invoice model and calls "
        "the tax service for each line.")
KEPT = [
    "Northwind refunds over $500 need a human approver from finance; the bot may only draft them.",
    "The Northwind sandbox payment provider account is shared with the mobile team; never reset its webhooks.",
    "Northwind release notes are written for customers, not engineers: no ticket numbers.",
    "Priya owns the Northwind tax-rate tables; ask her before changing a rate.",
    "Northwind's EU customers are billed from the Dublin entity, everyone else from Delaware.",
    "Dunning emails at Northwind go out at 09:00 in the customer's local time, never at night.",
]


def _seed(dsn: str, server_dir: Path) -> dict[str, str]:
    """Write the memories; returns statement -> id."""
    import sys

    import psycopg

    sys.path.insert(0, str(server_dir))
    from memvine_server.embeddings import FastEmbedEmbedder

    now = datetime.now(UTC)
    rows = [  # statement, kind, category, standing, subjects, days ago, review
        (RULE_OLD, "procedural", "rule", True, ["staging database", "migrations"], 12, "new"),
        (RULE_NEW, "procedural", "rule", True, ["migrations", "staging"], 3, "new"),
        (FACT_OLD, "semantic", "fact", False, ["invoice export job"], 20, "new"),
        (FACT_NEW, "semantic", "fact", False, ["invoice export job"], 2, "new"),
        (CODE, "semantic", "fact", False, ["InvoiceRenderer"], 4, "new"),
        *[(s, "semantic", "decision", False, [f"kept {i}"], 30, "kept") for i, s in enumerate(KEPT)],
    ]
    vectors = asyncio.run(FastEmbedEmbedder().embed([r[0] for r in rows]))
    ids = {}
    with psycopg.connect(dsn, autocommit=True) as conn:
        pid = conn.execute("SELECT id FROM projects WHERE slug = %s", (PROJECT,)).fetchone()[0]
        for n, ((statement, kind, category, standing, subjects, days, review), vector) in enumerate(zip(rows, vectors, strict=True)):
            at = now - timedelta(days=days)
            episode = f"tidy-ep-{n}"
            conn.execute("INSERT INTO episodes (project_id, id, content, content_hash, occurred_at, label) VALUES (%s, %s, %s, 'seed', %s, %s)",
                         (pid, episode, f"User: {statement}\nAssistant: Noted.", at, "seeded for the consolidation check"))
            mid = conn.execute(
                "INSERT INTO memory_items (project_id, kind, statement, subjects, valid_from, recorded_at, created_at, assertion, "
                "confidence, embedding, standing, category, origin, review) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, 'stated', 'high', %s::vector, %s, %s, 'synthesis', %s) RETURNING id",
                (pid, kind, statement, subjects, at, at, at, json.dumps(vector), standing, category, review)).fetchone()[0]
            conn.execute("INSERT INTO evidence (memory_id, project_id, episode_id, quote) VALUES (%s, %s, %s, %s)",
                         (mid, pid, episode, statement))
            conn.execute("INSERT INTO memory_events (project_id, memory_id, kind, actor, episode_id, at) VALUES (%s, %s, 'created', 'system', %s, %s)",
                         (pid, mid, episode, at))
            ids[statement] = str(mid)
    return ids


def run_section(call, check, mgmt: str, dsn: str, server_dir: Path) -> None:
    print("== 7. background consolidation (real fast model)", flush=True)
    call("POST", "/v1/projects", mgmt, {"name": PROJECT, "slug": PROJECT,
                                         "description": "Northwind billing: invoices, refunds, tax and how they are run"})
    key = call("POST", f"/v1/projects/{PROJECT}/keys", mgmt, {"name": "check"})["key"]
    ids = _seed(dsn, server_dir)
    before = {m["id"]: m for m in call("GET", "/v1/memories?limit=200", key)}
    queued = call("POST", f"/v1/projects/{PROJECT}/consolidate", mgmt)
    deadline, runs = time.time() + 900, []
    while time.time() < deadline:
        runs = call("GET", f"/v1/projects/{PROJECT}/consolidation-runs", mgmt)
        if runs and runs[0]["finished_at"]:
            break
        time.sleep(3)
    run = runs[0] if runs else {}
    check("the consolidation run finished without error", bool(run.get("finished_at")) and not run.get("error"),
          f"queued={queued.get('queued')} stats={json.dumps(run.get('stats'))} error={run.get('error')}")
    after = {m["id"]: m for m in call("GET", "/v1/memories?limit=200", key)}

    def show(statement):
        m = after.get(ids[statement], {})
        bits = [m.get("status"), m.get("retired_reason"), m.get("flag"), m.get("flag_reason")]
        return " ".join(str(b) for b in bits if b)

    old_rule, new_rule = after.get(ids[RULE_OLD], {}), after.get(ids[RULE_NEW], {})
    merged = ((new_rule.get("merged_into") == ids[RULE_OLD] and old_rule.get("status") == "active")
              or (old_rule.get("merged_into") == ids[RULE_NEW] and new_rule.get("status") == "active"))
    check("the two restatements of the rule are merged into one", merged,
          f"older: {show(RULE_OLD)}; newer: {show(RULE_NEW)}")
    old_fact = after.get(ids[FACT_OLD], {})
    linked = old_fact.get("status") == "superseded" and old_fact.get("superseded_by") == ids[FACT_NEW]
    flagged = old_fact.get("flag") == "possible_outdated"
    check("the outdated value is superseded by the new one, or flagged", linked or flagged, f"old fact: {show(FACT_OLD)}")
    code = after.get(ids[CODE], {})
    check("the code description is flagged no_longer_passes", code.get("flag") == "no_longer_passes" and code.get("status") == "active",
          show(CODE))
    kept_untouched = all(after.get(ids[s], {}).get("status") == "active" and not after.get(ids[s], {}).get("flag") for s in KEPT)
    check("memories a person kept are untouched", kept_untouched)
    others = [show(s) for s in (FACT_NEW,)]
    print(f"      new fact: {others[0]}", flush=True)
    check("nothing is deleted", set(before) == set(after) and len(after) == len(ids), f"{len(before)} before, {len(after)} after")
    feed = call("GET", "/v1/events?limit=50", key)["events"]
    kinds = sorted({e["kind"] for e in feed if e["actor"] == "consolidation"})
    check("every action is in the activity feed as consolidation", bool(kinds), ", ".join(kinds))
