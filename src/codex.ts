/** Project-scoped Codex onboarding. No global config or CLI installation required. */
import * as fs from 'node:fs';
import * as path from 'node:path';
import { randomUUID } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { parse, stringify } from 'smol-toml';

function rejectSymlink(target: string): void {
  try {
    if (fs.lstatSync(target).isSymbolicLink()) throw new Error(`Refusing symlink: ${target}`);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
  }
}

export function configureCodex(root: string): { file: string; created: boolean } {
  const directory = path.join(root, '.codex');
  const file = path.join(directory, 'config.toml');
  rejectSymlink(directory);
  rejectSymlink(file);
  const original = fs.existsSync(file) ? fs.readFileSync(file, 'utf8') : '';
  const config = parse(original);
  const server = {
    command: process.execPath,
    args: [fileURLToPath(new URL('./cli.js', import.meta.url)), 'serve'],
    cwd: root,
  };
  const servers = config.mcp_servers;
  if (servers !== undefined && (typeof servers !== 'object' || servers === null || Array.isArray(servers) || servers instanceof Date)) {
    throw new Error(`Invalid mcp_servers table in ${file}; existing file left unchanged.`);
  }
  const existing = (servers as Record<string, unknown> | undefined)?.memvine;
  if (existing !== undefined) {
    const entry = existing as Record<string, unknown>;
    if (entry && entry.command === server.command && entry.cwd === server.cwd &&
        JSON.stringify(entry.args) === JSON.stringify(server.args) && entry.enabled !== false) {
      return { file, created: false };
    }
    throw new Error(`Memvine is already configured differently in ${file}. Review that entry before rerunning setup; existing settings were not overwritten.`);
  }
  const addition = stringify({ mcp_servers: { memvine: server } });
  const updated = original + (original.endsWith('\n') || !original ? '' : '\n') +
    '\n# Memvine: generated for this machine and repository.\n' + addition;
  // Detect conflicts with inline tables/dotted keys before touching the file.
  parse(updated);
  fs.mkdirSync(directory, { recursive: true });
  const temporary = path.join(directory, `.memvine-${randomUUID()}.tmp`);
  try {
    const mode = fs.existsSync(file) ? fs.statSync(file).mode & 0o777 : 0o600;
    fs.writeFileSync(temporary, updated, { flag: 'wx', mode });
    fs.renameSync(temporary, file);
  } finally {
    if (fs.existsSync(temporary)) fs.rmSync(temporary);
  }
  return { file, created: true };
}
