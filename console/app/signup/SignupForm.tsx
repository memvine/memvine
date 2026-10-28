"use client";
import { useActionState, useEffect } from "react";
import { acceptInvite, signUp, type SignInState } from "@/app/actions";
import { button, Field, FormError, inputClass } from "@/components/ui";

export function SignupForm({ invite, inviteEmail }: { invite: string | null; inviteEmail: string | null }) {
  const [state, action, pending] = useActionState<SignInState, FormData>(signUp, {});
  useEffect(() => {
    if (state.ok) window.location.assign("/");
  }, [state.ok]);
  return (
    <form action={action} className="space-y-4">
      {invite && <input type="hidden" name="invite" value={invite} />}
      <Field label="Name" htmlFor="su-name">
        <input id="su-name" name="name" autoComplete="name" placeholder="Your name" className={inputClass} defaultValue={state.values?.name ?? ""} autoFocus />
      </Field>
      <Field label="Email" htmlFor="su-email">
        <input id="su-email" name="email" type="email" autoComplete="email" placeholder="you@company.com" className={inputClass}
               defaultValue={inviteEmail ?? state.values?.email ?? ""} readOnly={Boolean(inviteEmail)} required />
      </Field>
      <Field label="Password" htmlFor="su-password" hint="10 characters or more.">
        <input id="su-password" name="password" type="password" autoComplete="new-password" className={inputClass} minLength={10} required />
      </Field>
      <Field label="Password again" htmlFor="su-confirm">
        <input id="su-confirm" name="confirm" type="password" autoComplete="new-password" className={inputClass} required />
      </Field>
      {!invite && (
        <Field label="Organization name" htmlFor="su-org" hint="Optional.">
          <input id="su-org" name="organization" placeholder="Acme Inc" className={inputClass} defaultValue={state.values?.organization ?? ""} />
        </Field>
      )}
      {state.error && <FormError>{state.error}</FormError>}
      <button type="submit" disabled={pending} className={button("primary", "md", "h-9 w-full")}>{pending ? "Creating…" : "Create account"}</button>
    </form>
  );
}

/** For someone already signed in who opens an invite link. */
export function AcceptInviteForm({ invite }: { invite: string }) {
  const [state, action, pending] = useActionState<SignInState, FormData>(acceptInvite, {});
  useEffect(() => {
    if (state.ok) window.location.assign("/");
  }, [state.ok]);
  return (
    <form action={action} className="mt-4 space-y-3">
      <input type="hidden" name="invite" value={invite} />
      {state.error && <FormError>{state.error}</FormError>}
      <button type="submit" disabled={pending} className={button("primary", "md", "h-9 w-full")}>{pending ? "Joining…" : "Join with my account"}</button>
    </form>
  );
}

/** A signed-in person with nothing to accept goes to the console (a full load: its shell differs). */
export function GoHome() {
  useEffect(() => {
    window.location.assign("/");
  }, []);
  return <p className="text-[13px] text-text-2">You are signed in. Opening the console…</p>;
}
