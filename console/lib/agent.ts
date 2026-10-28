// What a key's last User-Agent says about the client that used it. The plugins send
// memvine-claude-code/<v> and memvine-codex/<v>; the SDKs memvine-sdk-ts/<v> and memvine-sdk-python/<v>.

/** The Claude Code plugin's current version (plugins/claude-code/.claude-plugin/plugin.json). */
export const PLUGIN_VERSION = "0.7.2";

const CLIENTS: Record<string, string> = {
  "memvine-claude-code": "Claude Code plugin",
  "memvine-codex": "Codex plugin",
  "memvine-sdk-ts": "TypeScript SDK",
  "memvine-sdk-python": "Python SDK",
};

export interface AgentInfo { label: string; version: string | null; plugin: boolean }

/** "Claude Code plugin 0.7.1" for memvine-claude-code/0.7.1; another client's agent string as it is. */
export function describeAgent(userAgent: string | null | undefined): AgentInfo | null {
  if (!userAgent) return null;
  const [name, version] = userAgent.split(/\s/)[0].split("/");
  const label = CLIENTS[name];
  if (label) return { label, version: version || null, plugin: name === "memvine-claude-code" || name === "memvine-codex" };
  return { label: userAgent.length > 60 ? `${userAgent.slice(0, 57)}…` : userAgent, version: null, plugin: false };
}

/** Whether a dotted version is older than another (0.6.9 < 0.7.1). */
export function olderThan(version: string, than: string): boolean {
  const a = version.split(".").map(Number), b = than.split(".").map(Number);
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    const d = (a[i] || 0) - (b[i] || 0);
    if (d !== 0) return d < 0;
  }
  return false;
}
