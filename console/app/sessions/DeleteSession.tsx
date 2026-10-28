"use client";
import { useActionState, useState } from "react";
import { Trash2 } from "lucide-react";
import { deleteSession, type DeleteSessionState } from "@/app/actions";
import { button } from "@/components/ui";

/** Two clicks: the first says what goes, the second deletes. */
export function DeleteSession({ id, label }: { id: string; label: string }) {
  const [state, action, pending] = useActionState<DeleteSessionState, FormData>(deleteSession, {});
  const [asking, setAsking] = useState(false);
  if (state.done) return <span className="text-text-2">{state.done}</span>;
  if (!asking) {
    return (
      <button type="button" className={button("ghost", "sm", "-ml-2 text-bad hover:bg-bad-soft hover:text-bad")} onClick={() => setAsking(true)}>
        <Trash2 aria-hidden /> Delete…
      </button>
    );
  }
  return (
    <form action={action} className="flex w-full flex-wrap items-center gap-2 rounded-lg border border-bad/30 bg-bad-soft px-3 py-2">
      <input type="hidden" name="id" value={id} />
      <span className="min-w-0 flex-1 text-[12.5px] text-text">Delete “{label}”, its messages and the memories only it supports?</span>
      <button disabled={pending} className={button("danger", "sm")}>{pending ? "Deleting…" : "Delete session"}</button>
      <button type="button" className={button("secondary", "sm")} onClick={() => setAsking(false)}>Cancel</button>
      {state.error && <span className="w-full text-bad">{state.error}</span>}
    </form>
  );
}
