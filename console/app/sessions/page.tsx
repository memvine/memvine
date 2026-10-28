import { MessagesSquare } from "lucide-react";
import { CopyButton } from "@/components/client";
import { Badge, Card, Empty, Eyebrow, formatDate, Mono, PageHeader, timeAgo } from "@/components/ui";
import { canManage, currentProject, listSessions } from "@/lib/api";
import { DeleteSession } from "./DeleteSession";

function StateList({ title, items, tone }: { title: string; items: string[]; tone?: "warn" }) {
  if (!items.length) return null;
  return (
    <div className="min-w-0">
      <Eyebrow className={tone === "warn" ? "text-warn" : ""}>{title}</Eyebrow>
      <ul className="mt-1.5 space-y-1 text-[13px] leading-snug text-text">
        {items.map((i) => (
          <li key={i} className="flex gap-2"><span className="mt-[7px] size-1 shrink-0 rounded-full bg-text-3" aria-hidden />{i}</li>
        ))}
      </ul>
    </div>
  );
}

export default async function SessionsPage() {
  const project = await currentProject();
  const [sessions, manage] = await Promise.all([listSessions(project.slug), canManage()]);
  const live = sessions.filter((s) => s.status === "live").length;

  return (
    <>
      <PageHeader
        icon={MessagesSquare}
        title="Sessions"
        count={sessions.length}
        description="Agent sessions streaming to this project. A live session keeps a working state the agent gets back after a compaction; once idle or ended, its turns become long-term memory."
        meta={live ? <Badge tone="good" dot>{live} live now</Badge> : undefined}
      />
      {sessions.length === 0 ? (
        <Card><Empty icon={MessagesSquare}>No sessions yet. Install the Claude Code plugin from Integrations to start streaming.</Empty></Card>
      ) : (
        <div className="space-y-4">
          {sessions.map((s) => (
            <Card key={s.id}>
              <div className="flex flex-wrap items-start gap-x-3 gap-y-1.5 px-4 pt-4 sm:px-5">
                <Badge tone={s.status === "live" ? "good" : "neutral"} dot>{s.status}</Badge>
                <div className="min-w-0 flex-1 basis-60">
                  <h2 className="text-[14px] font-medium text-text">{s.state.goal || s.label || s.id}</h2>
                  <p className="mt-0.5 truncate text-[12.5px] text-text-3">
                    {[s.label, s.identity].filter(Boolean).join(" · ")}
                  </p>
                </div>
                <div className="text-right text-[12.5px] text-text-3">
                  {s.turns} turns · {s.status === "live" ? `active ${timeAgo(s.lastEventAt)}` : formatDate(s.startedAt, true)}
                </div>
              </div>
              {s.state.goal || s.state.decisions.length || s.state.openQuestions.length ? (
                <div className="grid gap-x-6 gap-y-4 px-4 py-4 sm:grid-cols-2 sm:px-5 lg:grid-cols-3">
                  <StateList title="Decisions" items={s.state.decisions} />
                  <StateList title="Open questions" items={s.state.openQuestions} />
                  <StateList title="In play" items={s.state.inPlay} />
                  <StateList title="Recent facts" items={s.state.recentFacts} />
                  <StateList title="Unverified progress claims" items={s.state.progressClaims} tone="warn" />
                </div>
              ) : (
                <p className="px-4 py-4 text-[13px] text-text-3 sm:px-5">No working state yet; it is written after the first few turns.</p>
              )}
              <div className="flex min-h-11 flex-wrap items-center gap-x-4 gap-y-2 border-t border-border px-4 py-2 text-[12px] text-text-3 sm:px-5">
                <span>{s.status === "synthesized" ? "Turns became long-term memory" : `State reflects ${s.stateThrough} of ${s.turns} turns`}</span>
                {manage && <DeleteSession id={s.id} label={s.label || s.id} />}
                <span className="ml-auto flex items-center gap-1"><Mono className="text-text-3">{s.id}</Mono><CopyButton value={s.id} label="Copy session id" /></span>
              </div>
            </Card>
          ))}
        </div>
      )}
    </>
  );
}
