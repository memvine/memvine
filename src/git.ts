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
