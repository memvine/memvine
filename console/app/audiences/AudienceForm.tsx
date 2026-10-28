"use client";
import { useActionState, useState } from "react";
import { saveAudience, type AudienceFormState } from "@/app/actions";
import { button, FormError, inputClass, Select } from "@/components/ui";

export function AudienceForm() {
  const [state, action, pending] = useActionState<AudienceFormState, FormData>(saveAudience, {});
  const [kind, setKind] = useState<"fixed" | "rule">("fixed");

  return (
    <form action={action} className="space-y-3">
      <div className="flex flex-wrap gap-2">
        <input name="slug" placeholder="Audience slug, e.g. acme" aria-label="Audience slug" className={`${inputClass} max-w-56 font-mono`} />
        <Select name="kind" value={kind} onChange={(e) => setKind(e.target.value as "fixed" | "rule")} aria-label="Kind" wrapperClassName="w-[210px]">
          <option value="fixed">Fixed list of identities</option>
          <option value="rule">Rule on a trait</option>
        </Select>
      </div>
      {kind === "fixed" ? (
        <textarea name="members" rows={2} placeholder="Identities, one per line or comma-separated" aria-label="Members" className={inputClass} />
      ) : (
        <div className="flex flex-wrap items-center gap-2 text-[13px] text-text-2">
          <span>Every identity whose</span>
          <input name="trait" placeholder="trait, e.g. company" aria-label="Trait" className={`${inputClass} w-40`} />
          <span>is</span>
          <input name="value" placeholder="value, e.g. acme" aria-label="Value" className={`${inputClass} w-40`} />
        </div>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <button type="submit" disabled={pending} className={button("primary")}>{pending ? "Saving…" : "Save audience"}</button>
        {state.error && <FormError>{state.error}</FormError>}
        {state.saved && <p className="text-[12.5px] text-good">Saved “{state.saved}”.</p>}
      </div>
    </form>
  );
}
