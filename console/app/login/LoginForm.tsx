"use client";
import { useActionState, useEffect, useState } from "react";
import { signIn, type SignInState } from "@/app/actions";
import { button, Field, FormError, inputClass } from "@/components/ui";

type Mode = "email" | "key" | "invite";

export function LoginForm() {
  const [state, action, pending] = useActionState<SignInState, FormData>(signIn, {});
  const [mode, setMode] = useState<Mode>("email");
  useEffect(() => {
    if (state.ok) window.location.assign("/");
    else if (state.goTo) window.location.assign(state.goTo);
  }, [state.ok, state.goTo]);
  const other = (to: Mode, label: string) => (
    <button type="button" onClick={() => setMode(to)} className="block w-full rounded-md py-1 text-center text-[12.5px] text-text-3 transition-colors hover:text-text">{label}</button>
  );
  return (
    <form action={action} className="space-y-4">
      <div>
        <p className="text-[13.5px] font-medium text-text">Console sign-in</p>
        <p className="mt-0.5 text-[12.5px] text-text-3">
          {mode === "email" && "With your email and password. An invite link or an organization key works too."}
          {mode === "key" && <>With an organization key (<code className="font-mono">mv_mk_…</code>), for operators and shared admin access. Not an agent API key.</>}
          {mode === "invite" && "With the invite link an organization admin sent you."}
        </p>
      </div>
      {mode === "email" && (
        <>
          <Field label="Email" htmlFor="login-email">
            <input id="login-email" name="email" type="email" autoComplete="email" placeholder="you@company.com" className={inputClass} defaultValue={state.values?.email ?? ""} autoFocus required />
          </Field>
          <Field label="Password" htmlFor="login-password">
            <input id="login-password" name="password" type="password" autoComplete="current-password" className={inputClass} required />
          </Field>
        </>
      )}
      {mode === "key" && (
        <Field label="Organization key" htmlFor="login-key">
          <input id="login-key" name="key" type="password" autoComplete="off" placeholder="mv_mk_..." className={`${inputClass} font-mono`} autoFocus />
        </Field>
      )}
      {mode === "invite" && (
        <Field label="Invite link" htmlFor="login-invite">
          <input id="login-invite" name="invite" autoComplete="off" placeholder="Paste the invite link you were sent" className={inputClass} autoFocus required />
        </Field>
      )}
      {state.error && <FormError>{state.error}</FormError>}
      <button type="submit" disabled={pending} className={button("primary", "md", "h-9 w-full")}>
        {pending ? "Checking…" : mode === "invite" ? "Continue" : "Sign in"}
      </button>
      <div className="space-y-0.5 border-t border-border pt-3">
        {mode !== "invite" && other("invite", "I have an invite link")}
        {mode !== "email" && other("email", "Sign in with email instead")}
        {mode === "email" && <p className="text-center text-[12.5px] text-text-3">Forgot your password? Ask an owner or admin of your organization for a reset link.</p>}
        {mode === "email" && other("key", "Sign in with an organization key instead")}
      </div>
    </form>
  );
}
