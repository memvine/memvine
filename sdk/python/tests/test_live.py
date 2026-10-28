"""Runs against a live server: MEMVINE_TEST_URL and MEMVINE_TEST_KEY (a project key)."""
import os

import pytest

from memvine_sdk import Memvine, MemvineError

URL, KEY = os.environ.get("MEMVINE_TEST_URL"), os.environ.get("MEMVINE_TEST_KEY")
pytestmark = pytest.mark.skipif(not (URL and KEY), reason="needs MEMVINE_TEST_URL and MEMVINE_TEST_KEY")


def test_round_trip():
    with Memvine(KEY, base_url=URL) as mv:
        assert mv.ingest([{"id": "sdk-1", "content": "## Developer\nWe deploy on Tuesdays.", "timestamp": "2026-10-01T09:00:00Z"}])["items"]
        created = mv.remember("Always run the linter before committing.", kind="preference")
        assert created["memory"]["standing"] is True
        r = mv.recall("how do we commit code?")
        assert "Always run the linter" in r.briefing and r.standing
        s = mv.session("sdk-session", agent="sdk-test")
        s.send("user", "Let's use Postgres for the queue.")
        s.send("assistant", "Postgres it is.")
        assert s.state()["turns"] == 2
        assert s.brief().text.startswith("=== memvine")
        assert s.close()["sealed"] == [[1, 2]]
        with pytest.raises(MemvineError) as err:
            mv.memory("00000000-0000-0000-0000-000000000000")
        assert err.value.status == 404 and err.value.code == "not-found"
    bad = Memvine("mv_sk_nope", base_url=URL)
    with pytest.raises(MemvineError) as err:
        bad.recall("x")
    assert err.value.code == "unknown-api-key"
