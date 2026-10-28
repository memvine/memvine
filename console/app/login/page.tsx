import Link from "next/link";
import { redirect } from "next/navigation";
import { KeyRound } from "lucide-react";
import { isLive } from "@/lib/api";
import { AuthShell } from "@/components/AuthShell";
import { LoginForm } from "./LoginForm";

export const metadata = { title: "Sign in · Memvine Console" };

export default async function LoginPage() {
  if (!isLive || process.env.MEMVINE_MANAGEMENT_KEY) redirect("/");
  const { publicCall } = await import("@/lib/api-live");
  const config = await publicCall<{ signup: string }>("/v1/auth/config");
  const signup = config.ok ? config.data.signup : "closed";
  return (
    <AuthShell
      title="Sign in to the Memvine Console"
      subtitle="Memory for your agents, and the console to see it."
      footer={
        signup === "open" ? (
          <>New here? <Link href="/signup" className="font-medium text-text hover:underline">Create an account</Link></>
        ) : signup === "invite" ? (
          <span className="text-text-3">New accounts need an invite link from an organization admin.</span>
        ) : undefined
      }
      aside={
        <div className="rounded-xl border border-dashed border-border-strong px-5 py-4 text-[12.5px] leading-relaxed text-text-2">
          <p className="flex items-center gap-2 font-medium text-text">
            <KeyRound className="size-3.5 text-text-3" strokeWidth={1.75} aria-hidden />
            Agent API key <code className="font-mono text-[11.5px] font-normal text-text-3">mv_sk_…</code>
          </p>
          <p className="mt-1">
            Created inside the Console (API keys, per project) and used by agents and plugins to read and write one
            project&apos;s memory. It never signs in to the Console.
          </p>
        </div>
      }
    >
      <LoginForm />
    </AuthShell>
  );
}
