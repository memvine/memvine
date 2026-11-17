/** Thin git helpers. memvine's staleness engine is "just" git queries. */
import { execFileSync } from "node:child_process";

export function git(args: string[], cwd: string): string {
  return execFileSync("git", args, { cwd, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] }).trim();
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
    return git(["rev-parse", "HEAD"], cwd);
  } catch {
    return "0000000"; // fresh repo with no commits yet
  }
}

export interface ChangeScan {
  files: string[];
  unknown: boolean;
}

/** NUL-delimited paths preserve whitespace, Unicode and embedded newlines. */
export function inspectChangesSince(commit: string, cwd: string): ChangeScan {
  const files = new Set<string>();
  let unknown = false;
  const collect = (args: string[]) => {
    try {
      const output = execFileSync("git", args, { cwd, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
      for (const file of output.split("\0")) if (file) files.add(file);
    } catch { unknown = true; }
  };
  if (/^[a-f0-9]{7,40}$/.test(commit)) {
    // Disable rename coalescing so both old and new scoped paths are observed.
    collect(["diff", "--name-only", "-z", "--no-renames", `${commit}..HEAD`, "--"]);
  } else { unknown = true; }
  collect(["diff", "--name-only", "-z", "--no-renames", "HEAD", "--"]);
  collect(["diff", "--cached", "--name-only", "-z", "--no-renames", "--"]);
  collect(["ls-files", "--others", "--exclude-standard", "-z"]);
  return { files: [...files], unknown };
}

/** Compatibility helper; freshness decisions must use inspectChangesSince. */
export function changedFilesSince(commit: string, cwd: string): string[] {
  return inspectChangesSince(commit, cwd).files;
}

/** A file's content at a commit, or null if it did not exist there / git failed. */
export function fileAt(commit: string, file: string, cwd: string): string | null {
  if (!/^[a-f0-9]{7,40}$/.test(commit)) return null;
  try {
    return execFileSync("git", ["show", `${commit}:${file}`], { cwd, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"], maxBuffer: 64 * 1024 * 1024 });
  } catch { return null; }
}

export interface FileDiff {
  /** 0-based [start, end) line ranges of the OLD file that were replaced or deleted; an insertion is an empty range at its position. */
  oldRanges: [number, number][];
  /** Text of every added or removed line. */
  changedLines: string[];
}

/** Line-level diff of one file from `commit` to the working tree (committed + staged + unstaged). Null if git failed. */
export function diffFile(commit: string, file: string, cwd: string): FileDiff | null {
  let out: string;
  try {
    out = execFileSync("git", ["diff", "-U0", "--no-renames", commit, "--", file], { cwd, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"], maxBuffer: 64 * 1024 * 1024 });
  } catch { return null; }
  const oldRanges: [number, number][] = [];
  const changedLines: string[] = [];
  for (const line of out.split("\n")) {
    const h = /^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@/.exec(line);
    if (h) {
      const start = Number(h[1]), count = h[2] === undefined ? 1 : Number(h[2]);
      // For count 0 git reports the line BEFORE the insertion point.
      oldRanges.push(count === 0 ? [start, start] : [start - 1, start - 1 + count]);
    } else if ((line.startsWith("+") && !line.startsWith("+++")) || (line.startsWith("-") && !line.startsWith("---"))) {
      changedLines.push(line.slice(1));
    }
  }
  return { oldRanges, changedLines };
}

/** Git blob id of a working-tree file, or null if it can't be hashed (missing, unreadable). */
export function hashFile(file: string, cwd: string): string | null {
  try {
    return execFileSync("git", ["hash-object", "--", file], { cwd, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] }).trim() || null;
  } catch { return null; }
}
