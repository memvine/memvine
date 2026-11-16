"use client";
// The team spaces a memory of the current project can be shared with: the organization's team
// spaces the viewer can open, none when the current project is itself a team space. Set once in
// the layout, read by every receipt.
import { createContext, useActionState, useContext, type ReactNode } from "react";
import { Share2, Undo2 } from "lucide-react";
import { shareMemory, unshareMemory, type ReceiptState } from "@/app/actions";
import type { MemoryItem } from "@/lib/types";
import { button, cx, inputClass } from "./ui";

export interface TeamSpaceRef { slug: string; name: string }

const TeamSpacesContext = createContext<TeamSpaceRef[]>([]);

export function TeamSpacesProvider({ spaces, children }: { spaces: TeamSpaceRef[]; children: ReactNode }) {
  return <TeamSpacesContext.Provider value={spaces}>{children}</TeamSpacesContext.Provider>;
}

export const useTeamSpaces = () => useContext(TeamSpacesContext);

/** Share with team, or take back, for a memory shown outside a receipt (its drawer and its page).
 *  Nothing is rendered in a team space, or when the organization has no team space to share with. */
export function ShareActions({ m, size = "md" }: { m: MemoryItem; size?: "sm" | "md" }) {
  const spaces = useTeamSpaces();
  const [shareState, shareAction, sharing] = useActionState<ReceiptState, FormData>(shareMemory, {});
  const [unshareState, unshareAction, unsharing] = useActionState<ReceiptState, FormData>(unshareMemory, {});
  if (m.status !== "active" && m.status !== "stale") return null;
  const said = shareState.error ?? unshareState.error ?? (m.sharedRef ? shareState.note : unshareState.note);
  const note = said ? <span className="self-center text-[12px] text-text-2" role="status">{said}</span> : null;
  if (m.sharedRef) {
    return (
      <>
        <form action={unshareAction}>
          <input type="hidden" name="id" value={m.id} />
          <button type="submit" disabled={unsharing} className={button("secondary", size)}
                  title="Take it back from the team space. It stays there when a teammate shared it too.">
            <Undo2 aria-hidden /> {unsharing ? "Taking back…" : "Shared · take back"}
          </button>
        </form>
        {note}
      </>
    );
  }
  if (spaces.length === 0) return null;
  return (
    <>
      <form action={shareAction} className="flex items-center gap-1.5">
        <input type="hidden" name="id" value={m.id} />
        {spaces.length === 1 ? <input type="hidden" name="team" value={spaces[0].slug} /> : (
          <select name="team" aria-label="Team space" className={cx(inputClass, "h-8 w-auto py-0 text-[12.5px]")}>
            {spaces.map((t) => <option key={t.slug} value={t.slug}>{t.name}</option>)}
          </select>
        )}
        <button type="submit" disabled={sharing} className={button("secondary", size)}
                title={`Copy it to ${spaces.length === 1 ? spaces[0].name : "the team space"}: the statement, who said it, when and the quoted line. Nothing else of the session goes.`}>
          <Share2 aria-hidden /> {sharing ? "Sharing…" : "Share with team"}
        </button>
      </form>
      {note}
    </>
  );
}
