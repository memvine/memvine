#!/usr/bin/env node
// Claude Code transcripts as the plugin would send them, one JSON object per line on stdout:
// {session, path, cwd, branch, turns: [{role, content, at}]}. Uses the plugin's own parser, so
// what capture.py measures is exactly what the server would receive.
//
//   node benchmark/capture/export.mjs [--no-tools] <transcript.jsonl>...
import { basename } from "node:path";
import { readTranscript } from "../../plugins/claude-code/bin/memvine-hook.mjs";

const args = process.argv.slice(2);
const outcomes = !args.includes("--no-tools");
for (const path of args.filter((a) => !a.startsWith("--"))) {
  const { turns, branch, cwd } = readTranscript(path, { outcomes });
  process.stdout.write(JSON.stringify({ session: basename(path, ".jsonl"), path, cwd, branch, turns }) + "\n");
}
