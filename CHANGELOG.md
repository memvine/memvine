# Release notes

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

Known limits: generated digests are snapshots; dirty scoped files remain suspect
after validation until committed/revalidated; unscoped facts are unchecked;
verification depends on the caller. Writes are atomic per file, not locked
multi-file transactions. Agent capture behavior and real coding benefit still
need independent sessions and held-out evaluation.
