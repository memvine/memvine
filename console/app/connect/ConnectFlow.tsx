"use client";
import Link from "next/link";
import { useActionState, useEffect, useRef, useState, type ReactNode } from "react";
import { ArrowRight, Check, ChevronsUpDown, Code2, KeyRound, Loader2, Plus, Server, Terminal, Truck } from "lucide-react";
import { checkKeyConnection, createKey, switchProject, type CreateKeyState } from "@/app/actions";
import { CopyButton } from "@/components/client";
import { button, Card, cx, FormError, inputClass, timeAgo } from "@/components/ui";
import { describeAgent, olderThan, PLUGIN_VERSION } from "@/lib/agent";
import type { KeyConnection } from "@/lib/types";

interface KeyRef { id: string; name: string; masked: string; createdAt: string }

const PLACEHOLDER = "mv_sk_...";
type Client = "claude-code" | "codex" | "mcp" | "sdk";
const CLIENTS: { id: Client; label: string; icon: typeof Terminal }[] = [
  { id: "claude-code", label: "Claude Code", icon: Terminal },
  { id: "codex", label: "Codex CLI", icon: Terminal },
  { id: "mcp", label: "Any MCP client", icon: Server },
  { id: "sdk", label: "SDK", icon: Code2 },
];

export function ConnectFlow({ project, projects, apiUrl, email, mine, otherKeys }: {
  project: { slug: string; name: string };
  projects: { slug: string; name: string }[];
  apiUrl: string;
  email: string;
  mine: KeyRef | null;
  otherKeys: number;
}) {
  const [state, action, pending] = useActionState<CreateKeyState, FormData>(createKey, {});
  const [identity, setIdentity] = useState(email);
  const [client, setClient] = useState<Client>("claude-code");
  const [connected, setConnected] = useState(false);
  const switcher = useRef<HTMLFormElement>(null);

  const secret = state.secret ?? null;
  const key = secret ?? PLACEHOLDER;
  const who = identity.trim() || email;
  const watched: KeyRef | null = state.id && state.name
    ? { id: state.id, name: state.name, masked: `mv_sk_••••${state.secret?.slice(-4) ?? ""}`, createdAt: new Date().toISOString() }
    : mine;
  const keyName = `agent (${email.split("@")[0] || "me"})`.slice(0, 60);

  if (!project.slug) {
    return (
      <Card className="p-5 text-[13.5px] text-text-2">
        Agents connect to a project. <Link href="/projects" className="font-medium text-text underline underline-offset-2">Create your first project</Link>, then come back here.
      </Card>
    );
  }

  return (
    <div className="max-w-4xl space-y-4">
      {/* 1 · project */}
      <Step n={1} done title="Pick the project" description="Agents connected to the same project share one memory.">
        <form ref={switcher} action={switchProject} className="relative max-w-sm">
          <label htmlFor="connect-project" className="sr-only">Project</label>
          <select id="connect-project" name="project" defaultValue={project.slug} onChange={() => switcher.current?.requestSubmit()}
                  className={cx(inputClass, "cursor-pointer appearance-none pr-8 font-medium")}>
            {projects.map((p) => <option key={p.slug} value={p.slug}>{p.name}</option>)}
          </select>
          <ChevronsUpDown className="pointer-events-none absolute top-1/2 right-2.5 size-3.5 -translate-y-1/2 text-text-3" aria-hidden />
        </form>
      </Step>

      {/* 2 · key */}
      <Step n={2} done={Boolean(secret || mine)} title="Get your key"
            description={`A project key lets your agent send and recall memory in ${project.name}. It is yours: one per person and machine is easiest to manage.`}>
        {secret ? (
          <div className="rounded-xl border border-accent/30 bg-accent-soft p-4">
            <p className="flex items-center gap-2 text-[13.5px] font-medium text-text"><KeyRound className="size-4 text-accent" aria-hidden /> Key “{state.name}” created</p>
            <p className="mt-0.5 text-[12.5px] text-text-2">Copy it somewhere safe now: it is not shown again. The commands below already include it.</p>
            <div className="mt-3 flex items-center gap-2">
              <code className="min-w-0 flex-1 truncate rounded-lg border border-border bg-surface px-3 py-[7px] font-mono text-[12.5px] text-text">{secret}</code>
              <CopyButton value={secret} label="Copy key" showLabel />
            </div>
          </div>
        ) : mine ? (
          <div className="space-y-3 text-[13px] text-text-2">
            <p>
              You have a key for this project: <span className="font-medium text-text">{mine.name}</span>{" "}
              <code className="font-mono text-[12px] text-text-3">{mine.masked}</code>, created {timeAgo(mine.createdAt)}.
              Its secret was shown once, when you made it; the steps below show <code className="font-mono">{PLACEHOLDER}</code> where it goes.
            </p>
            <CreateButton action={action} pending={pending} name={keyName} label="Lost it? Create a new key" variant="secondary" error={state.error} />
          </div>
        ) : (
          <div className="space-y-2 text-[13px] text-text-2">
            <p>You don’t have a key for {project.name} yet{otherKeys > 0 ? ` (the project has ${otherKeys} other ${otherKeys === 1 ? "key" : "keys"}, made by teammates or services)` : ""}.</p>
            <CreateButton action={action} pending={pending} name={keyName} label="Create my key" variant="primary" error={state.error} />
          </div>
        )}
        <div className="mt-4 max-w-sm">
          <label htmlFor="identity" className="mb-1 block text-[12.5px] font-medium text-text">Your identity</label>
          <input id="identity" value={identity} onChange={(e) => setIdentity(e.target.value)} className={inputClass} autoComplete="email" />
          <p className="mt-1 text-[12px] text-text-3">What you say in sessions is attributed to this. Your sign-in email unless you change it.</p>
        </div>
      </Step>

      {/* 3 · install */}
      <Step n={3} done={connected} title="Install it in your agent" description={<>Copy and paste, in order. The API address is <code className="font-mono text-text">{apiUrl}</code>.</>}>
        <div role="tablist" aria-label="Agent" className="mb-4 inline-flex max-w-full flex-wrap gap-0.5 rounded-lg border border-border bg-surface-2 p-0.5">
          {CLIENTS.map((c) => {
            const IconC = c.icon;
            const active = client === c.id;
            return (
              <button key={c.id} type="button" role="tab" aria-selected={active} onClick={() => setClient(c.id)}
                      className={cx("inline-flex h-7 items-center gap-1.5 rounded-md px-2.5 text-[13px] transition-colors",
                        active ? "bg-surface font-medium text-text shadow-[0_1px_2px_rgb(0_0_0/0.08)] ring-1 ring-border" : "text-text-2 hover:text-text")}>
                <IconC className="size-3.5" aria-hidden /> {c.label}
              </button>
            );
          })}
        </div>
        <div role="tabpanel">
          {client === "claude-code" && <ClaudeCode apiUrl={apiUrl} keyValue={key} who={who} />}
          {client === "codex" && <Codex apiUrl={apiUrl} keyValue={key} who={who} />}
          {client === "mcp" && <Mcp apiUrl={apiUrl} keyValue={key} />}
          {client === "sdk" && <Sdk apiUrl={apiUrl} keyValue={key} who={who} />}
        </div>
        {!secret && <p className="mt-4 text-[12px] text-text-3">Replace <code className="font-mono">{PLACEHOLDER}</code> with your key. Never commit it to the repository.</p>}
      </Step>

      {/* 4 · check */}
      <Step n={4} done={connected} title="Check the connection" description="Start a session in your agent and ask it anything. This turns green when the first session or recall arrives.">
        <ConnectionCheck keyRef={watched} onConnected={() => setConnected(true)} />
      </Step>

      {/* moving servers */}
      <Card className="p-4 sm:p-5">
        <div className="flex items-start gap-3">
          <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-surface-2 text-text-2"><Truck className="size-4" aria-hidden /></span>
          <div className="min-w-0 flex-1 text-[13px] text-text-2">
            <p className="text-[13.5px] font-medium text-text">Moving to a new server</p>
            <p className="mt-0.5">
              If your plugin was connected to an older address (a team server on someone’s laptop, say), point it at{" "}
              <code className="font-mono text-text">{apiUrl}</code>. Your key stays valid; only the address changes. In the repository, in Claude Code:
            </p>
            <div className="mt-2"><Snippet>{`/memvine:connect ${key} ${who} ${apiUrl} --here`}</Snippet></div>
            <p className="mt-2">Codex CLI: run the install again with the new address; it replaces the old one.</p>
            <div className="mt-2"><Snippet>{`node ~/.memvine/codex/bin/memvine-codex.mjs install ${key} ${who} ${apiUrl}`}</Snippet></div>
            <p className="mt-2 text-[12px] text-text-3">
              Then <code className="font-mono">/memvine:status</code> should say “connected to {apiUrl}”. An MCP client: change the <code className="font-mono">url</code> in its config to <code className="font-mono">{apiUrl}/mcp</code>.
            </p>
          </div>
        </div>
      </Card>
    </div>
  );
}

function CreateButton({ action, pending, name, label, variant, error }: {
  action: (f: FormData) => void; pending: boolean; name: string; label: string; variant: "primary" | "secondary"; error?: string;
}) {
  return (
    <form action={action}>
      <input type="hidden" name="name" value={name} />
      <button type="submit" disabled={pending} className={button(variant)}>
        {pending ? <Loader2 className="animate-spin" aria-hidden /> : <Plus aria-hidden />} {pending ? "Creating…" : label}
      </button>
      {error && <div className="mt-2"><FormError>{error}</FormError></div>}
    </form>
  );
}

function Step({ n, title, description, done = false, children }: { n: number; title: string; description?: ReactNode; done?: boolean; children: ReactNode }) {
  return (
    <Card>
      <div className="flex gap-3 px-4 py-4 sm:gap-4 sm:px-5">
        <span className={cx("mt-px grid size-6 shrink-0 place-items-center rounded-full text-[12px] font-semibold",
          done ? "bg-accent text-accent-text" : "border border-border-strong text-text-2")} aria-hidden>
          {done ? <Check className="size-3.5" strokeWidth={3} /> : n}
        </span>
        <div className="min-w-0 flex-1">
          <h2 className="text-[14px] font-medium text-text"><span className="sr-only">Step {n}: </span>{title}</h2>
          {description && <p className="mt-0.5 text-[12.5px] leading-relaxed text-text-3">{description}</p>}
          <div className="mt-3">{children}</div>
        </div>
      </div>
    </Card>
  );
}

function Snippet({ children }: { children: string }) {
  return (
    <div className="relative">
      <pre className="scroll-quiet overflow-x-auto rounded-lg border border-border bg-surface-2 p-3 pr-10 font-mono text-[12px] leading-relaxed text-text">{children}</pre>
      <div className="absolute top-2 right-2"><CopyButton value={children} label="Copy" /></div>
    </div>
  );
}

function Sub({ n, children, code, after }: { n: number; children: ReactNode; code?: string; after?: ReactNode }) {
  return (
    <li className="flex gap-3">
      <span className="w-4 shrink-0 pt-px text-right text-[12.5px] tabular-nums text-text-3">{n}.</span>
      <div className="min-w-0 flex-1 space-y-2 text-[13px] text-text-2">
        <p>{children}</p>
        {code && <Snippet>{code}</Snippet>}
        {after && <p className="text-[12px] text-text-3">{after}</p>}
      </div>
    </li>
  );
}

function ClaudeCode({ apiUrl, keyValue, who }: { apiUrl: string; keyValue: string; who: string }) {
  return (
    <ol className="space-y-4">
      <Sub n={1} code={"/plugin marketplace add memvine/memvine\n/plugin install memvine@memvine"}>In Claude Code, add the plugin’s marketplace and install it:</Sub>
      <Sub n={2}>Restart Claude Code (quit it and run <code className="font-mono text-text">claude</code> again) so the plugin’s hooks load.</Sub>
      <Sub n={3} code={`/memvine:connect ${keyValue} ${who} ${apiUrl} --here`}
           after={<><code className="font-mono">--here</code> connects only this repository. Leave it out to make this project your default everywhere.</>}>
        Open the repository this project is for, and connect it:
      </Sub>
      <Sub n={4} code="/memvine:status" after={`It should print: memvine ${PLUGIN_VERSION} · connected to ${apiUrl} as ${who}`}>Check it:</Sub>
    </ol>
  );
}

function Codex({ apiUrl, keyValue, who }: { apiUrl: string; keyValue: string; who: string }) {
  return (
    <ol className="space-y-4">
      <Sub n={1} code={`git clone --depth 1 https://github.com/memvine/memvine ~/.memvine/src && node ~/.memvine/src/plugins/codex/bin/memvine-codex.mjs install ${keyValue} ${who} ${apiUrl}`}
           after="Needs Node 18+ and Codex CLI 0.155 or later. It registers the MCP server and five session hooks in ~/.codex.">
        In a terminal, run this one command:
      </Sub>
      <Sub n={2}>Start <code className="font-mono text-text">codex</code>, type <code className="font-mono text-text">/hooks</code> and trust the five memvine hooks. Codex skips a new hook until you trust it.</Sub>
      <Sub n={3} code="node ~/.memvine/codex/bin/memvine-codex.mjs status">Check it:</Sub>
    </ol>
  );
}

function Mcp({ apiUrl, keyValue }: { apiUrl: string; keyValue: string }) {
  const config = JSON.stringify({ mcpServers: { memvine: { url: `${apiUrl}/mcp`, headers: { Authorization: `Bearer ${keyValue}` } } } }, null, 2);
  return (
    <ol className="space-y-4">
      <Sub n={1} code={config}
           after="Streamable HTTP. In Cursor this goes in .cursor/mcp.json; other clients have their own place for MCP servers.">
        Add Memvine as an MCP server. The URL is <code className="font-mono text-text">{apiUrl}/mcp</code> and the key goes in the <code className="font-mono text-text">Authorization: Bearer</code> header:
      </Sub>
      <Sub n={2} code="Before acting on anything that depends on an earlier decision, a person, a date or a value, call memory_recall with the question. When a decision is made or a rule is agreed, call memory_remember with one self-contained sentence.">
        An MCP agent calls memory only when it decides to, so add this to its instructions (AGENTS.md, .cursor/rules):
      </Sub>
    </ol>
  );
}

function Sdk({ apiUrl, keyValue, who }: { apiUrl: string; keyValue: string; who: string }) {
  const [lang, setLang] = useState<"ts" | "py">("ts");
  const ts = `import { Memvine } from "@memvine/sdk";
const mv = new Memvine({ apiKey: process.env.MEMVINE_KEY!, baseUrl: process.env.MEMVINE_URL });

const s = mv.session("demo-1", { agent: "my-agent", identity: "${who}" });
await s.send("user", "Retries cap at 3: the broker rate-limits us above that.");
await s.send("assistant", "Set MAX_RETRIES = 3 in src/queue.py.");
await s.close();                                  // tending turns the session into memory

const r = await mv.recall("How many retries do we allow?");
console.log(r.briefing);                          // hand this to your model as background`;
  const py = `import os
from memvine_sdk import Memvine
mv = Memvine(os.environ["MEMVINE_KEY"], base_url=os.environ["MEMVINE_URL"])

s = mv.session("demo-1", agent="my-agent", identity="${who}")
s.send("user", "Retries cap at 3: the broker rate-limits us above that.")
s.send("assistant", "Set MAX_RETRIES = 3 in src/queue.py.")
s.close()                                  # tending turns the session into memory

r = mv.recall("How many retries do we allow?")
print(r.briefing)                          # hand this to your model as background`;
  return (
    <ol className="space-y-4">
      <Sub n={1} code={`export MEMVINE_KEY=${keyValue}\nexport MEMVINE_URL=${apiUrl}`}>Set the key and address in your environment:</Sub>
      <Sub n={2} code={lang === "ts"
             ? "git clone https://github.com/memvine/memvine.git && npm install ./memvine/sdk/typescript"
             : "git clone https://github.com/memvine/memvine.git && pip install ./memvine/sdk/python"}
           after={<>The SDKs are not on {lang === "ts" ? "npm" : "PyPI"} yet, so they install from the repository.</>}>
        Install the SDK{" "}
        <span className="ml-1 inline-flex rounded-lg border border-border p-0.5 align-middle">
          {(["ts", "py"] as const).map((l) => (
            <button key={l} type="button" onClick={() => setLang(l)} aria-pressed={lang === l}
                    className={cx("rounded-md px-2 py-0.5 text-[12px]", lang === l ? "bg-surface-3 font-medium text-text" : "text-text-3 hover:text-text")}>
              {l === "ts" ? "TypeScript" : "Python"}
            </button>
          ))}
        </span>
      </Sub>
      <Sub n={3} code={lang === "ts" ? ts : py}>Send a session and recall from it:</Sub>
    </ol>
  );
}

/** Polls the key's first use every 5 seconds (while the tab is visible) until a session or recall arrives. */
function ConnectionCheck({ keyRef, onConnected }: { keyRef: KeyRef | null; onConnected: () => void }) {
  const [conn, setConn] = useState<KeyConnection | null>(null);
  const [checkedAt, setCheckedAt] = useState<number | null>(null);
  const [, tick] = useState(0);
  const connected = Boolean(conn && (conn.firstSessionAt || conn.firstRecallAt));
  const id = keyRef?.id;

  useEffect(() => {
    if (!id || connected) return;
    let stop = false;
    const poll = async () => {
      if (document.visibilityState !== "visible") return;
      try {
        const c = await checkKeyConnection(id);
        if (!stop) { setConn(c); setCheckedAt(Date.now()); }
      } catch { /* a failed poll: the next one tries again */ }
    };
    poll();
    const t = window.setInterval(poll, 5000);
    return () => { stop = true; window.clearInterval(t); };
  }, [id, connected]);

  // Keeps "2 min ago" current once connected.
  useEffect(() => {
    if (connected) onConnected();
  }, [connected, onConnected]);
  useEffect(() => {
    if (!connected) return;
    const t = window.setInterval(() => tick((n) => n + 1), 30_000);
    return () => window.clearInterval(t);
  }, [connected]);

  if (!keyRef) {
    return <p className="text-[13px] text-text-3">Create your key in step 2 first; this watches for its first session.</p>;
  }

  const agent = describeAgent(conn?.lastUserAgent);
  const outdated = agent?.plugin && agent.version && olderThan(agent.version, PLUGIN_VERSION);

  if (connected && conn) {
    const first = conn.firstSessionAt ?? conn.firstRecallAt!;
    const what = conn.firstSessionAt ? "first session received" : "first recall";
    return (
      <div className="rounded-xl border border-good/30 bg-good-soft p-4" aria-live="polite">
        <p className="flex items-center gap-2 text-[14px] font-medium text-good">
          <span className="grid size-5 place-items-center rounded-full bg-good text-white"><Check className="size-3" strokeWidth={3} aria-hidden /></span>
          Connected: {what} {timeAgo(first)}
        </p>
        <p className="mt-1 text-[12.5px] text-text-2">
          Key <span className="font-medium text-text">{keyRef.name}</span>
          {agent && <> · {agent.label}{agent.version && ` ${agent.version}`}</>}
          {conn.lastUsedAt && <> · last call {timeAgo(conn.lastUsedAt)}</>}
        </p>
        {outdated && (
          <p className="mt-2 text-[12.5px] text-warn">
            A newer plugin is out ({PLUGIN_VERSION}). Run <code className="font-mono">/plugin marketplace update memvine</code> in Claude Code, then restart it.
          </p>
        )}
        <div className="mt-3 flex flex-wrap gap-2">
          <Link href="/sessions" className={button("secondary", "sm")}>See sessions <ArrowRight aria-hidden /></Link>
          <Link href="/playground" className={button("ghost", "sm")}>Ask memory a question</Link>
        </div>
      </div>
    );
  }

  const usedOnly = conn && conn.lastUsedAt;
  return (
    <div className="flex items-start gap-3 rounded-xl border border-border bg-surface-2 p-4" aria-live="polite">
      <span className="relative mt-1.5 flex size-2.5 shrink-0" aria-hidden>
        <span className="absolute inline-flex size-full animate-ping rounded-full bg-info opacity-60" />
        <span className="relative inline-flex size-2.5 rounded-full bg-info" />
      </span>
      <div className="min-w-0 text-[13px]">
        <p className="font-medium text-text">
          {usedOnly ? `Key in use: last call ${timeAgo(conn!.lastUsedAt)}${agent ? ` from ${agent.label}${agent.version ? ` ${agent.version}` : ""}` : ""}` : `Waiting for the first session from “${keyRef.name}”…`}
        </p>
        <p className="mt-0.5 text-[12.5px] text-text-3">
          {usedOnly
            ? "Still waiting for a session or recall. Start a session and send a message."
            : "Start a session in your agent and type a message of a sentence or more."}{" "}
          {checkedAt ? `Checked ${new Date(checkedAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}; checking every 5 s.` : "Checking…"}
        </p>
      </div>
    </div>
  );
}
