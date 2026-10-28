// A banner on Home and Sources when synthesis is not keeping up in the current project: sources
// that failed recently, a queue that stopped moving, or a worker that stopped polling
// (GET /v1/health/synthesis). Renders nothing when all is well or the viewer may not see it.
import Link from "next/link";
import { Callout } from "@/components/ui";
import { synthesisHealth } from "@/lib/api";
import type { SynthesisHealth } from "@/lib/types";

const SEE_SOURCES = " — see Sources";

export async function SynthesisBanner({ project, onSources = false }: { project: string; onSources?: boolean }) {
  let health: SynthesisHealth | null = null;
  try {
    health = await synthesisHealth(project);
  } catch {
    return null; // the banner never takes the page down with it
  }
  if (!health || health.healthy) return null;
  const severe = health.issues.some((i) => i.condition !== "failed");
  return (
    <div className="mb-5" role="status">
      <Callout tone={severe ? "bad" : "warn"} title={severe ? "Synthesis is not keeping up" : "Some sources were not synthesized"}>
        <ul className="space-y-0.5">
          {health.issues.map((i) => {
            const linked = i.message.endsWith(SEE_SOURCES);
            const text = linked ? i.message.slice(0, -SEE_SOURCES.length) : i.message;
            return (
              <li key={i.condition}>
                {text}
                {linked && !onSources && (
                  <> — see <Link href="/sources" className="underline underline-offset-2 hover:text-text">Sources</Link></>
                )}
                {linked && onSources && "; retry them below."}
              </li>
            );
          })}
        </ul>
      </Callout>
    </div>
  );
}
