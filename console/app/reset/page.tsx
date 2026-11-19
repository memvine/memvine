import Link from "next/link";
import { redirect } from "next/navigation";
import { isLive } from "@/lib/api";
import { AuthShell } from "@/components/AuthShell";
import { ResetForm } from "./ResetForm";

export const metadata = { title: "Set a new password · Memvine Console" };

/** Where a password reset link lands: from an organization admin (Settings → Reset a password) or the
 *  server's operator (`memvine-server reset-link`). No sign-in needed; the link itself is the proof. */
export default async function ResetPage({ searchParams }: { searchParams: Promise<{ token?: string }> }) {
  if (!isLive || process.env.MEMVINE_MANAGEMENT_KEY) redirect("/");
  const { token } = await searchParams;
  const { publicCall } = await import("@/lib/api-live");
  const details = token ? await publicCall<{ email: string }>("/v1/auth/reset/describe", { token }) : null;
  return (
    <AuthShell
      title="Set a new password"
      subtitle={details?.ok ? <>For <strong className="font-medium text-text">{details.data.email}</strong>.</> : null}
      footer={<>Remembered it? <Link href="/login" className="font-medium text-text hover:underline">Sign in</Link></>}
    >
      {!token ? (
        <p className="text-[13px] text-text-2">Open the reset link you were sent. To get one, ask an owner or admin of your organization.</p>
      ) : !details?.ok ? (
        <p role="alert" className="text-[13px] text-bad">{details?.message ?? "This reset link does not work."}</p>
      ) : (
        <ResetForm token={token} />
      )}
    </AuthShell>
  );
}
