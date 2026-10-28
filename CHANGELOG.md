# Release notes

## 0.3.0 (unreleased)

- Add `memvine digest`: a session briefing of the whole store (full text while the
  byte budget allows, then titles), next steps and a `status` note first, stale and
  unconfirmed memories labelled. `--json` lists shown ids for hooks. In a multi-session
  test only ~7 of 25 stored memories ever reached the agent through per-file top-k recall.
- Add `recall --exclude <ids>` so a repeated recall surfaces memories not yet shown.
- Add `memvine retire <id...>` and `memvine add --supersedes <id>`.
- Anchor staleness to the identifiers a memory names: only changes inside those
  identifiers' blocks (or changed lines mentioning them) stale it. Memories naming
  no code keep file-level staleness. Keywords and everyday words are not anchors.
- Memories learned or validated while scoped files are dirty record content hashes
  (`validated_snapshot`) and are no longer born stale; they turn suspect if the file
  changes again or the checked edit is discarded.
- `remember` guidance asks for exact observations and retiring finished next steps.
- `add -t/-s` also accept comma-separated values (a later space-separated value
  beginning with `-` is otherwise parsed as an option).

## Unreleased alpha (source version 0.2.1)

The npm 0.0.1 package was a placeholder. The first usable CLI release must use
an available version and will initially be published under the `next` tag.
This checkout has not been published by the release-preparation work.

- Add `memvine init --codex` for project-local MCP registration with preserved settings and conflict detection.
- Derive scoped freshness during recall and compilation, including dirty and
  untracked files. Treat unavailable Git history as unknown, not fresh.
- Keep unverified replacements local; publish before removing candidates or
  retiring shared predecessors. Write memory files atomically.
- Preserve learning provenance and record a separate validation timestamp.
- Bound rendered recall/digest bytes; add paged `read_memory` and `doctor`.
- Validate stored metadata/config and reject memory symlink paths.
- Keep BM25; abstain on unmatched query-only searches and guide query refinement.
- Build during npm pack, ship declarations/image and omit tests/source maps.
- Fix cached YAML metadata mutation after failed writes; clone parsed metadata before edits.
- Update seven vulnerable transitive dependencies within existing version ranges.
- Add real MCP lifecycle tests, installed-tarball checks and a CI matrix.

Runtime declaration is now Node >=22. CI must pass before advertising tested
support beyond the local Node 22.22.0/macOS run. Older valid memories without
verification/validation fields are read using documented location/commit
fallbacks. Malformed files are skipped by recall and reported by doctor.

Known limits: generated digests are snapshots; unscoped facts are unchecked;
verification depends on the caller. Writes are atomic per file, not locked
multi-file transactions. Agent capture behavior and real coding benefit still
need independent sessions and held-out evaluation.
