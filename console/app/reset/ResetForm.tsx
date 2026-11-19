"use client";
import Link from "next/link";
import { useActionState } from "react";
import { resetPassword, type PasswordState } from "@/app/actions";
import { button, Field, FormError, inputClass } from "@/components/ui";

export function ResetForm({ token }: { token: string }) {
  const [state, action, pending] = useActionState<PasswordState, FormData>(resetPassword, {});
  if (state.ok) {
    return (
      <div className="space-y-4">
        <p className="text-[13px] text-text-2">Your password is set, and every other sign-in has ended. Sign in with the new password.</p>
        <Link href="/login" className={button("primary", "md", "h-9 w-full")}>Sign in</Link>
      </div>
    );
  }
  return (
    <form action={action} className="space-y-4">
      <input type="hidden" name="token" value={token} />
      <Field label="New password" htmlFor="rs-password" hint="10 characters or more.">
        <input id="rs-password" name="password" type="password" autoComplete="new-password" className={inputClass} minLength={10} required autoFocus />
      </Field>
      <Field label="New password again" htmlFor="rs-confirm">
        <input id="rs-confirm" name="confirm" type="password" autoComplete="new-password" className={inputClass} required />
      </Field>
      {state.error && <FormError>{state.error}</FormError>}
      <button type="submit" disabled={pending} className={button("primary", "md", "h-9 w-full")}>{pending ? "Saving…" : "Set password"}</button>
    </form>
  );
}
