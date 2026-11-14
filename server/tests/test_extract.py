from datetime import UTC, datetime

from memvine_server.extraction.extract import candidates_from, parse_json, quote_in_source

SOURCE = (
    "## Developer · 2026-10-03T14:02\nLegal pushed back on the refund copy, so we're moving launch from 29 Oct to 12 Nov.\n\n"
    "## Claude · 2026-10-03T14:03\nUnderstood. Implementation complete, all tests pass."
)


def test_parse_json_strips_fences_and_prose():
    assert parse_json('Here you go:\n```json\n[{"a": 1}]\n```') == [{"a": 1}]
    assert parse_json('{"relation": "same"} trailing') == {"relation": "same"}


def test_quote_check_is_whitespace_and_quote_insensitive():
    assert quote_in_source("we’re moving   launch from 29 Oct", SOURCE)
    assert not quote_in_source("moving launch to 13 Nov", SOURCE)
    assert not quote_in_source("Oct", SOURCE)  # too short to count as evidence


def test_candidates_drop_unquoted_and_downgrade_agent_claims():
    raw = [
        {
            "statement": "Launch date is 12 Nov 2026, moved from 29 Oct because legal pushed back on the refund copy.",
            "kind": "semantic", "subjects": ["Launch", " legal "], "assertion": "stated", "confidence": "high",
            "valid_from": "2026-10-03", "quote": "moving launch from 29 Oct to 12 Nov",
        },
        {
            "statement": "The escalation implementation is complete and all tests pass.",
            "kind": "episodic", "subjects": ["escalation"], "assertion": "stated", "confidence": "high",
            "quote": "Implementation complete, all tests pass.",
        },
        {
            "statement": "Pricing page copy is blocked on legal.",
            "kind": "prospective", "subjects": ["pricing page"], "assertion": "inferred",
            "quote": "pricing page copy is blocked on legal",  # not in the source
        },
        {"statement": "", "kind": "semantic", "assertion": "stated", "quote": "launch"},
        {"statement": "Bad kind", "kind": "fact", "assertion": "stated", "quote": "launch from 29 Oct"},
    ]
    result = candidates_from(raw, SOURCE)

    assert [c.statement[:6] for c in result.items] == ["Launch", "The es"]
    assert result.dropped_no_quote == 1
    assert result.dropped_invalid == 2

    launch, claim = result.items
    assert launch.subjects == ["launch", "legal"]
    assert launch.valid_from == datetime(2026, 10, 3)
    # An assistant's "all tests pass" is never stored as a stated fact, whatever the model said.
    assert claim.assertion == "claimed"
    assert claim.confidence == "medium"


def test_candidates_dedupe_identical_statements():
    raw = [
        {"statement": "Launch is 12 Nov.", "kind": "semantic", "assertion": "stated", "quote": "launch from 29 Oct to 12 Nov"},
        {"statement": "launch is 12 nov.", "kind": "semantic", "assertion": "stated", "quote": "launch from 29 Oct to 12 Nov"},
    ]
    assert len(candidates_from(raw, SOURCE).items) == 1


def test_valid_from_accepts_full_timestamps():
    raw = [{"statement": "x y z", "kind": "semantic", "assertion": "stated", "quote": "refund copy",
            "valid_from": "2026-10-03T14:02:00Z"}]
    assert candidates_from(raw, SOURCE).items[0].valid_from == datetime(2026, 10, 3, 14, 2, tzinfo=UTC)


def test_reconcile_prompt_numbers_existing_items():
    from memvine_server.extraction.prompts import reconcile_user

    text = reconcile_user("Launch is 12 Nov.", "2026-10-03", [("2026-09-18", "Launch is 29 Oct."), ("2026-09-20", "Legal reviews copy.")])
    assert text.splitlines() == [
        "EXISTING 1 (2026-09-18): Launch is 29 Oct.",
        "EXISTING 2 (2026-09-20): Legal reviews copy.",
        "NEW (2026-10-03): Launch is 12 Nov.",
    ]


def test_parse_json_repairs_escapes_and_salvages_elements():
    from memvine_server.extraction.extract import parse_json

    # Invalid escape (Windows path) and a raw newline inside a string.
    assert parse_json('[{"statement": "Config lives in C:\\Users\\craig\\app.ini", "x": "a\nb"}]')[0]["x"] == "a\nb"
    # One malformed element among good ones: the good ones survive.
    raw = '[\n {"statement": "one", "kind": "semantic"},\n {"statement": "two" "kind": oops},\n {"statement": "three", "kind": "episodic"}\n]'
    assert [d["statement"] for d in parse_json(raw)] == ["one", "three"]
    # Truncated output: complete elements are kept.
    assert [d["statement"] for d in parse_json('[{"statement": "a"}, {"statement": "b"}, {"statem')] == ["a", "b"]
    # Code fence wrapping the whole reply still works; backticks inside statements are left alone.
    assert parse_json('```json\n[{"statement": "Point a `CNAME` record"}]\n```')[0]["statement"] == "Point a `CNAME` record"


def test_split_passages_packs_turns_and_splits_long_ones():
    from memvine_server.passages import split_passages

    short = "## Craig · t1\nHello.\n\n## Assistant · t2\nHi there."
    assert split_passages(short) == ["## Craig · t1\nHello.\n\n## Assistant · t2\nHi there."], "small turns pack together"
    long_turn = "## Craig · t1\n" + " ".join(f"Sentence number {i} is here." for i in range(200))
    pieces = split_passages(long_turn, size=500)
    assert len(pieces) > 5 and all(len(p) <= 520 for p in pieces)
    assert pieces[0].startswith("## Craig · t1") and pieces[1].startswith("## Craig · t1 (cont.)")
    assert split_passages("plain text without headers") == ["plain text without headers"]
    assert split_passages(short, pack=False) == ["## Craig · t1\nHello.", "## Assistant · t2\nHi there."], "one passage per turn"


def test_standing_rules_are_flagged():
    from memvine_server.extraction.extract import is_standing

    assert is_standing(None, "preference", "Craig prefers that code snippets always use syntax highlighting.")
    assert is_standing(True, "preference", "Answer in British English.")
    assert not is_standing(False, "preference", "He always liked tea.")  # the model's own judgement wins
    assert not is_standing(None, "preference", "Craig wants POST /transactions to return 201.")
    assert not is_standing(True, "semantic", "The deadline is always the 15th."), "only preferences and procedures"


def test_diversify_prefers_new_topics_over_near_duplicates():
    import numpy as np

    from memvine_server.recall import diversify

    def vec(*xs):
        v = np.array(xs + (0.0,) * (384 - len(xs)), dtype=np.float32)
        return v / np.linalg.norm(v)

    rows = {
        "a1": {"embedding": vec(1, 0.00, 0)}, "a2": {"embedding": vec(1, 0.02, 0)}, "a3": {"embedding": vec(1, 0.04, 0)},
        "db": {"embedding": vec(0.3, 1, 0)}, "csrf": {"embedding": vec(0.3, 0, 1)},
    }
    ranked = [(1.0, "a1"), (0.98, "a2"), (0.96, "a3"), (0.7, "db"), (0.65, "csrf")]
    broad = [m for _, m in diversify(ranked, rows, 0.6)]
    assert broad[:3] == ["a1", "db", "csrf"], "after the best fact, distinct topics beat near-duplicates"
    narrow = [m for _, m in diversify(ranked, rows, 0.0)]
    assert narrow == ["a1", "a2", "a3", "db", "csrf"], "lambda 0 keeps relevance order"
