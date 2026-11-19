"use client";
import { useActionState } from "react";
import { KeyRound, UserPlus } from "lucide-react";
import { changePassword, createInvite, createResetLink, type InviteState, type PasswordState, type ResetLinkState } from "@/app/actions";
import { SecretReveal } from "@/components/SecretReveal";
import { button, FormError, inputClass, Select } from "@/components/ui";

export function InviteForm() {
  const [state, run, pending] = useActionState<InviteState, FormData>(createInvite, {});
  if (state.link) {
    return (
      <SecretReveal
        title="Invite link created"
        note="Send it to your teammate. It works once, for 7 days, and is not shown again."
        secret={state.link}
      />
    );
  }
  return (
    <form action={run} className="flex flex-wrap items-start gap-2">
      <div className="min-w-56 flex-1">
        <input name="email" type="email" placeholder="Their email (optional: limits the link to that address)" aria-label="Email" className={inputClass} />
        {state.error && <div className="mt-1.5"><FormError>{state.error}</FormError></div>}
      </div>
      <Select name="role" aria-label="Role" wrapperClassName="w-[120px]" defaultValue="member">
        <option value="member">Member</option>
        <option value="admin">Admin</option>
      </Select>
      <button type="submit" disabled={pending} className={button("primary")}><UserPlus aria-hidden /> {pending ? "Creating…" : "Create invite link"}</button>
    </form>
  );
}

export function PasswordForm() {
  const [state, run, pending] = useActionState<PasswordState, FormData>(changePassword, {});
  return (
    <form action={run} className="grid gap-2 sm:grid-cols-[1fr_1fr_1fr_auto]">
      <input name="current" type="password" autoComplete="current-password" placeholder="Current password" aria-label="Current password" className={inputClass} required />
      <input name="new" type="password" autoComplete="new-password" placeholder="New password" aria-label="New password" className={inputClass} minLength={10} required />
      <input name="confirm" type="password" autoComplete="new-password" placeholder="New password again" aria-label="Confirm new password" className={inputClass} required />
      <button type="submit" disabled={pending} className={button("secondary")}>{pending ? "Saving…" : "Change password"}</button>
      {state.error && <div className="sm:col-span-4"><FormError>{state.error}</FormError></div>}
      {state.ok && <p className="text-[12.5px] text-good sm:col-span-4">Password changed. Other sign-ins were ended.</p>}
    </form>
  );
}

/** For a member who cannot sign in: a one-time link to set a new password, to pass on to them. */
export function ResetLinkForm({ people }: { people: { id: string; email: string }[] }) {
  const [state, run, pending] = useActionState<ResetLinkState, FormData>(createResetLink, {});
  if (state.link) {
    return (
      <SecretReveal
        title={`Reset link for ${state.email}`}
        note="Send it to them yourself, over a channel you trust. It works once, for 24 hours, ends their other sign-ins, and is not shown again."
        secret={state.link}
      />
    );
  }
  return (
    <form action={run} className="flex flex-wrap items-start gap-2">
      <Select name="member" aria-label="Person who cannot sign in" wrapperClassName="min-w-56 flex-1" defaultValue="">
        <option value="" disabled>Someone who cannot sign in…</option>
        {people.map((p) => <option key={p.id} value={`${p.id}|${p.email}`}>{p.email}</option>)}
      </Select>
      <button type="submit" disabled={pending} className={button("secondary")}><KeyRound aria-hidden /> {pending ? "Creating…" : "Create reset link"}</button>
      {state.error && <div className="basis-full"><FormError>{state.error}</FormError></div>}
    </form>
  );
}
