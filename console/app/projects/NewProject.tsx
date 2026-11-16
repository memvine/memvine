"use client";
import { useActionState, useState } from "react";
import { Plus } from "lucide-react";
import { createProject, openProject, type ProjectFormState } from "@/app/actions";
import { CommandBlock, SecretReveal } from "@/components/SecretReveal";
import { button, cx, FormError, inputClass } from "@/components/ui";

const KINDS = [
  { value: "personal", title: "Only me", text: "Your own project. Your sessions go here and become your memory. Nobody else in the organization can open it, whatever their role." },
  { value: "team", title: "Team space", text: "Holds only the memories people choose to share from projects of their own, and refuses sessions: nobody's conversation can be read here." },
  { value: "shared", title: "Everyone's sessions", text: "Every member's sessions go here, and anyone in the organization can read them. For a team that wants one open memory." },
] as const;

export function NewProject({ apiUrl, email, canManage, person }: { apiUrl: string; email: string; canManage: boolean; person: boolean }) {
  // A member creates only their own project; a key (no person behind it) cannot own one.
  const kinds = KINDS.filter((k) => (k.value === "personal" ? person : canManage));
  const [kind, setKind] = useState<string>(kinds[0]?.value ?? "shared");
  const [open, setOpen] = useState(false);
  const [state, action, pending] = useActionState<ProjectFormState, FormData>(createProject, {});

  if (state.created) {
    const { slug, name, secret, teamSpace } = state.created;
    return (
      <div className="w-full">
        <SecretReveal
          title={`${teamSpace ? "Team space" : "Project"} “${name}” created`}
          note={teamSpace
            ? "Its first API key is below. Copy it now; it will not be shown again. Give it to each teammate: in Claude Code, after connecting a project of their own, they run:"
            : "Its first API key is below. Copy it now; it will not be shown again. In Claude Code, in the repository this project is for:"}
          secret={secret}
          copyLabel="Copy key"
          actions={
            <form action={openProject}>
              <input type="hidden" name="slug" value={slug} />
              <button className={button("secondary")}>Open project</button>
            </form>
          }
        >
          <CommandBlock>{teamSpace ? `/memvine:team ${secret} ${apiUrl}` : `/memvine:connect ${secret} ${email} ${apiUrl} --here`}</CommandBlock>
        </SecretReveal>
      </div>
    );
  }

  if (!open) {
    return <button type="button" className={button("primary")} onClick={() => setOpen(true)}><Plus aria-hidden /> New project</button>;
  }

  return (
    <form action={action} className="w-full space-y-3 rounded-xl border border-border bg-surface p-4 sm:p-5">
      <div>
        <p className="text-[14px] font-medium text-text">New project</p>
        <p className="mt-0.5 text-[13px] text-text-2">A project holds the memory of one app, repository or team. Nothing crosses between projects, except what a person shares from their own project into a team space.</p>
      </div>
      <div className="flex flex-wrap gap-2">
        <input name="name" placeholder="Name, e.g. Billing service" aria-label="Project name" className={`${inputClass} max-w-xs`} autoFocus />
        <input name="tags" placeholder="Tags, comma-separated (optional)" aria-label="Tags" className={`${inputClass} max-w-xs`} />
      </div>
      <div>
          <label htmlFor="new-project-description" className="mb-1.5 block text-[12.5px] font-medium text-text-2">What is this project about? (optional)</label>
          <textarea id="new-project-description" name="description" rows={2} maxLength={2000}
                    placeholder="e.g. The billing service: invoices, refunds and its releases"
                    className={`${inputClass} resize-y leading-relaxed`} />
          <p className="mt-1 text-[12px] text-text-3">Memvine judges what is worth keeping against this description.</p>
      </div>
      <label className="flex items-start gap-2 text-[13px] text-text-2">
        <input type="checkbox" name="sample" className="mt-0.5 accent-current" />
        <span>
          <span className="font-medium text-text">Start with sample data</span>
          <span className="block text-[12px] text-text-3">A few sources and memories to try recall on before your agent is connected. Free, and deletable in one click.</span>
        </span>
      </label>
      <fieldset className="space-y-2">
        <legend className="mb-1.5 block text-[12.5px] font-medium text-text-2">Who is it for? (cannot be changed later)</legend>
        <div className="grid gap-2 lg:grid-cols-3">
          {kinds.map((k) => (
            <label key={k.value} className={cx("flex cursor-pointer gap-2.5 rounded-lg border px-3 py-2.5", kind === k.value ? "border-text-2" : "border-border")}>
              <input type="radio" name="kind" value={k.value} checked={kind === k.value} onChange={() => setKind(k.value)} className="mt-1" />
              <span>
                <span className="block text-[13px] font-medium text-text">{k.title}</span>
                <span className="mt-0.5 block text-[12.5px] text-text-2">{k.text}</span>
              </span>
            </label>
          ))}
        </div>
      </fieldset>
      <div className="flex flex-wrap gap-2">
        <button type="submit" disabled={pending} className={button("primary")}>{pending ? "Creating…" : "Create project"}</button>
        <button type="button" className={button("ghost")} onClick={() => setOpen(false)}>Cancel</button>
      </div>
      {state.error && <FormError>{state.error}</FormError>}
    </form>
  );
}
