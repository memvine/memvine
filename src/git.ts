/** Thin git helpers. memvine's staleness engine is "just" git queries. */
import { execFileSync } from "node:child_process";

export function git(args: string[], cwd: string): string {
  return execFileSync("git", args, { cwd, encoding: "utf8" }).trim();
}

export function isGitRepo(cwd: string): boolean {
  try {
    git(["rev-parse", "--is-inside-work-tree"], cwd);
    return true;
  } catch {
    return false;
  }
}

export function repoRoot(cwd: string): string {
  return git(["rev-parse", "--show-toplevel"], cwd);
}

export function headCommit(cwd: string): string {
  try {
    return git(["rev-parse", "--short", "HEAD"], cwd);
  } catch {
    return "0000000"; // fresh repo with no commits yet
  }
}

/**
 * Files changed between a commit and HEAD (plus uncommitted changes).
 * Returns [] if the base commit is unknown to this clone (shallow clone,
 * rebase, etc.) — in that case we can't judge staleness, so we stay quiet
 * rather than crying wolf.
 */
export function changedFilesSince(commit: string, cwd: string): string[] {
  try {
    const committed = git(["diff", "--name-only", `${commit}..HEAD`], cwd);
    const uncommitted = git(["diff", "--name-only", "HEAD"], cwd);
    const all = new Set(
      [...committed.split("\n"), ...uncommitted.split("\n")].filter(Boolean),
    );
    return [...all];
  } catch {
    return [];
  }
}
