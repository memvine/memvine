import { CircleDot, GitPullRequest, Hash, Rocket, Ticket } from "lucide-react";
import type { ThreadKind } from "@/lib/types";
import { cx, THREAD_KIND_LABEL, type Icon } from "./ui";

export const THREAD_KIND_ICON: Record<ThreadKind, Icon> = {
  release: Rocket,
  task: CircleDot,
  pr: GitPullRequest,
  ticket: Ticket,
  topic: Hash,
};

/** Kind icon and label, e.g. "Release". */
export function ThreadKindTag({ kind, className = "" }: { kind: ThreadKind; className?: string }) {
  const KindIcon = THREAD_KIND_ICON[kind];
  return (
    <span className={cx("inline-flex items-center gap-1.5 text-[12.5px] whitespace-nowrap text-text-2", className)}>
      <KindIcon className="size-3.5 text-text-3" strokeWidth={1.75} aria-hidden />
      {THREAD_KIND_LABEL[kind]}
    </span>
  );
}
