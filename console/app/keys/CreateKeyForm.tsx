"use client";
import { useActionState } from "react";
import { Plus } from "lucide-react";
import { createKey, type CreateKeyState } from "@/app/actions";
import { CommandBlock, SecretReveal } from "@/components/SecretReveal";
import { button, FormError, inputClass } from "@/components/ui";

export function CreateKeyForm({ apiUrl, email }: { apiUrl: string; email: string }) {
  const [state, action, pending] = useActionState<CreateKeyState, FormData>(createKey, {});

  if (state.secret) {
    return (
      <SecretReveal
        title={`Key “${state.name}” created`}
        note="Copy it now; it will not be shown again. To connect Claude Code, run this in the repository this project is for:"
        secret={state.secret}
      >
        <CommandBlock>{`/memvine:connect ${state.secret} ${email} ${apiUrl} --here`}</CommandBlock>
        <p className="mt-2 text-[12px] text-text-3">For another agent or a server, store it as <code className="font-mono">MEMVINE_API_KEY</code>.</p>
      </SecretReveal>
    );
  }

  return (
    <form action={action} className="flex flex-wrap items-start gap-2">
      <div className="min-w-56 flex-1">
        <label htmlFor="key-name" className="sr-only">Key name</label>
        <input id="key-name" name="name" placeholder="Key name, e.g. production" className={inputClass} maxLength={60} />
        {state.error && <div className="mt-1.5"><FormError>{state.error}</FormError></div>}
      </div>
      <button type="submit" disabled={pending} className={button("primary")}><Plus aria-hidden /> {pending ? "Creating…" : "Create key"}</button>
    </form>
  );
}
