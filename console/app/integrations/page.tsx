import { Braces, Plug, Server, Terminal } from "lucide-react";
import { CopyButton } from "@/components/client";
import { Card, CardBody, CardHeader, PageHeader } from "@/components/ui";
import { currentProject, publicApiUrl, viewerEmail } from "@/lib/api";

function Snippet({ children }: { children: string }) {
  return (
    <div className="relative">
      <pre className="scroll-quiet overflow-x-auto rounded-lg border border-border bg-surface-2 p-3 pr-10 font-mono text-[12px] leading-relaxed text-text">{children}</pre>
      <div className="absolute top-2 right-2"><CopyButton value={children} label="Copy snippet" /></div>
    </div>
  );
}

export default async function IntegrationsPage() {
  const [project, email] = await Promise.all([currentProject(), viewerEmail()]);
  const base = publicApiUrl();

  return (
    <>
      <PageHeader
        icon={Plug}
        title="Integrations"
        description={`Connect any agent to ${project.name}. Agents in this project share one memory: they can add conversations and ask for relevant context later.`}
      />

      <Card className="mb-6 border-border-strong bg-surface-2">
        <CardBody className="grid gap-5 sm:grid-cols-3 sm:gap-6">
          <div>
            <p className="text-[12px] font-medium uppercase tracking-[0.05em] text-text-3">1 · Connect</p>
            <p className="mt-1 text-[13px] leading-relaxed text-text-2">Choose MCP for an agent that supports it, or use the API for your own app.</p>
          </div>
          <div>
            <p className="text-[12px] font-medium uppercase tracking-[0.05em] text-text-3">2 · Learn</p>
            <p className="mt-1 text-[13px] leading-relaxed text-text-2">Memvine turns conversations into sourced memories you can review and correct.</p>
          </div>
          <div>
            <p className="text-[12px] font-medium uppercase tracking-[0.05em] text-text-3">3 · Recall</p>
            <p className="mt-1 text-[13px] leading-relaxed text-text-2">Agents ask a question and get a focused briefing from the project’s memory.</p>
          </div>
        </CardBody>
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card className="min-w-0 lg:col-span-2">
          <CardHeader icon={Server} title="MCP server" description="For any MCP-compatible agent or assistant" />
          <CardBody className="space-y-3 text-[13px] text-text-2">
            <p>Give an agent access to <code className="font-mono text-text">memory_recall</code> and <code className="font-mono text-text">memory_remember</code>. Use the same project key wherever you want agents to share memory.</p>
            <p className="text-[12px] font-medium text-text-3">HTTP server details · client setup varies</p>
            <Snippet>{JSON.stringify({ mcpServers: { memvine: { url: `${base}/mcp`, headers: { Authorization: "Bearer mv_sk_..." } } } }, null, 2)}</Snippet>
            <p className="text-[12px] text-text-3">Replace <code className="font-mono">mv_sk_...</code> with a project API key from API keys. The key gives the agent access to project memory; it does not sign in to the Console.</p>
          </CardBody>
        </Card>

        <Card className="min-w-0">
          <CardHeader icon={Terminal} title="Claude Code plugin" description="Automatic session capture for Claude Code" />
          <CardBody className="space-y-3 text-[13px] text-text-2">
            <p>Sends each session&apos;s prompts and replies (never tool output or file contents), and briefs every new session from memory.</p>
            <Snippet>{`/plugin marketplace add memvine/memvine
/plugin install memvine@memvine
/memvine:connect mv_sk_... ${email} ${base} --here`}</Snippet>
            <p className="text-[12px] text-text-3">Create the <code className="font-mono">mv_sk_</code> key on the API keys screen. <code className="font-mono">--here</code> binds this repository to the project; leave it out to make it your default.</p>
          </CardBody>
        </Card>

        <Card className="min-w-0">
          <CardHeader icon={Terminal} title="Codex CLI" description="Automatic session capture for Codex CLI" />
          <CardBody className="space-y-3 text-[13px] text-text-2">
            <p>Two commands add the plugin; the third registers the MCP server and the session hooks in <code className="font-mono">~/.codex</code>. Sends what you typed and Codex&apos;s final answers, never tool output or file contents.</p>
            <Snippet>{`codex plugin marketplace add memvine/memvine
codex plugin add memvine@memvine
node "$(ls -dt ~/.codex/plugins/cache/memvine/memvine/*/ | head -1)codex/bin/memvine-codex.mjs" install mv_sk_... ${email} ${base}`}</Snippet>
            <p className="text-[12px] text-text-3">Then start <code className="font-mono">codex</code>, type <code className="font-mono">/hooks</code> and trust the five memvine hooks; Codex runs a new hook only once you trust it.</p>
          </CardBody>
        </Card>

        <Card className="min-w-0 lg:col-span-2">
          <CardHeader icon={Braces} title="REST API" description="For agents in your product, backend workflows and custom integrations" />
          <CardBody className="space-y-3 text-[13px] text-text-2">
            <p>Send conversation as episodes; recall returns a dated briefing within a token budget.</p>
            <Snippet>{`curl ${base}/v1/ingest \\
  -H "Authorization: Bearer $MEMVINE_API_KEY" -H "Content-Type: application/json" \\
  -d '{"items":[{"id":"chat-42:1","content":"...","timestamp":"2026-10-25T09:00:00Z","identity":"user-17"}]}'

curl ${base}/v1/recall \\
  -H "Authorization: Bearer $MEMVINE_API_KEY" -H "Content-Type: application/json" \\
  -d '{"query":"When is the launch?","identity":"user-17","budget":2000}'`}</Snippet>
          </CardBody>
        </Card>
      </div>
    </>
  );
}
