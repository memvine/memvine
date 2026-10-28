"use client";
import { useActionState } from "react";
import { Plus } from "lucide-react";
import type { SecretState } from "@/app/actions";
import { SecretReveal } from "@/components/SecretReveal";
import { button, FormError, inputClass } from "@/components/ui";

/** A one-field create form whose result is a secret shown once. */
export function SecretForm({ action, placeholder, button: label, note }: {
  action: (prev: SecretState, fd: FormData) => Promise<SecretState>;
  placeholder: string;
  button: string;
  note: string;
}) {
  const [state, run, pending] = useActionState<SecretState, FormData>(action, {});
  if (state.secret) {
    return <SecretReveal title={`Created ${state.label}`} note={note} secret={state.secret} />;
  }
  return (
    <form action={run} className="flex flex-wrap items-start gap-2">
      <div className="min-w-56 flex-1">
        <input name="name" placeholder={placeholder} aria-label={placeholder} className={inputClass} maxLength={100} />
        {state.error && <div className="mt-1.5"><FormError>{state.error}</FormError></div>}
      </div>
      <button type="submit" disabled={pending} className={button("primary")}><Plus aria-hidden /> {pending ? "Creating…" : label}</button>
    </form>
  );
}
