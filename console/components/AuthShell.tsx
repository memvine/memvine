import type { ReactNode } from "react";
import { Logo } from "./Brand";

/** The centered card of the sign-in and sign-up pages. */
export function AuthShell({ title, subtitle, children, footer, aside }: { title: string; subtitle?: ReactNode; children: ReactNode; footer?: ReactNode; aside?: ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center py-12">
      <div className="w-full max-w-[400px]">
        <div className="mb-8 flex flex-col items-center text-center">
          <Logo className="size-10" />
          <h1 className="mt-5 text-[20px] font-semibold tracking-[-0.02em] text-text">{title}</h1>
          {subtitle && <p className="mt-1.5 text-[13.5px] text-text-2">{subtitle}</p>}
        </div>
        <div className="rounded-xl border border-border bg-surface p-6 shadow-panel sm:p-7">{children}</div>
        {aside && <div className="mt-4">{aside}</div>}
        {footer && <div className="mt-6 text-center text-[13px] text-text-2">{footer}</div>}
      </div>
    </div>
  );
}
