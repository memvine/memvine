"""Secrets never reach storage, embeddings or a model: every text the API accepts (sources,
session turns, memories, recall queries) passes through redact() first. The patterns match the
Claude Code plugin's (plugins/claude-code/bin/memvine-hook.mjs), so a client that forgets to
redact, or does not redact at all (an SDK, an MCP client), is covered here."""
from __future__ import annotations

import re

_KEY_PREFIXES = r"mv_sk|mv_mk|mv_us|mv_in|sk-ant|sk-|ghp_|gho_|ghu_|ghs_|github_pat|xox[baprs]|AKIA|ASIA|glpat"
PATTERNS: list[re.Pattern] = [
    re.compile(rf"\b(?:{_KEY_PREFIXES})-?[A-Za-z0-9_\-]{{12,}}"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{20,}", re.IGNORECASE),
    re.compile(r"\beyJ[A-Za-z0-9._\-]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
]
# NAME=value where the name says secret: keep the name, drop the value.
ASSIGNMENT = re.compile(r"\b([A-Z0-9_]*(?:SECRET|PASSWORD|TOKEN|API_?KEY|PRIVATE_?KEY|CREDENTIAL)[A-Z0-9_]*)\s*[=:]\s*\S+")


def redact(text: str) -> str:
    out = ASSIGNMENT.sub(lambda m: f"{m.group(1)}=[redacted]", text)
    for pattern in PATTERNS:
        out = pattern.sub("[redacted]", out)
    return out
