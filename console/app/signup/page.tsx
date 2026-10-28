import Link from "next/link";
import { redirect } from "next/navigation";
import { isLive } from "@/lib/api";
import { AuthShell } from "@/components/AuthShell";
import { AcceptInviteForm, GoHome, SignupForm } from "./SignupForm";

export const metadata = { title: "Create an account · Memvine Console" };

export default async function SignupPage({ searchParams }: { searchParams: Promise<{ invite?: string }> }) {
  if (!isLive || process.env.MEMVINE_MANAGEMENT_KEY) redirect("/");
  const { invite } = await searchParams;
  const { publicCall, managementKey, whoIs } = await import("@/lib/api-live");
  const config = await publicCall<{ signup: string }>("/v1/auth/config");
  const mode = config.ok ? config.data.signup : "closed";
  const details = invite ? await publicCall<{ organization: string; role: string; email: string | null }>("/v1/auth/invites/describe", { invite }) : null;
  const me = await whoIs(await managementKey());

  let subtitle: React.ReactNode = null;
  let body: React.ReactNode;
  if (me?.user && !(details?.ok)) {
    // Also where a sign-up lands: the action sets the cookie and this page renders again.
    body = <GoHome />;
  } else if (details && !details.ok) {
    body = <p role="alert" className="text-[13px] text-bad">{details.message}</p>;
  } else if (details?.ok && me?.user) {
    subtitle = <>Join <strong className="font-medium text-text">{details.data.organization}</strong> as {details.data.role === "admin" ? "an admin" : "a member"}.</>;
    body = (
      <>
        <p className="text-[13px] text-text-2">You are signed in as {me.user.email}.</p>
        <AcceptInviteForm invite={invite!} />
      </>
    );
  } else if (mode === "closed" || (mode === "invite" && !details)) {
    body = <p className="text-[13px] text-text-2">{mode === "closed" ? "Sign-up is closed on this server." : "New accounts need an invite link from an organization admin."}</p>;
  } else {
    subtitle = details?.ok
      ? <>You were invited to join <strong className="font-medium text-text">{details.data.organization}</strong> as {details.data.role === "admin" ? "an admin" : "a member"}.</>
      : "You get your own organization; invite teammates to it from Settings.";
    body = <SignupForm invite={details?.ok ? invite! : null} inviteEmail={details?.ok ? details.data.email : null} />;
  }
  return (
    <AuthShell
      title={details?.ok ? "Join your team on Memvine" : "Create an account"}
      subtitle={subtitle}
      footer={
        <>
          Have an account? <Link href="/login" className="font-medium text-text hover:underline">Sign in</Link>
          {invite && details?.ok && !me?.user && <span className="mt-1 block text-[12px] text-text-3">then open the invite link again to join.</span>}
        </>
      }
    >
      {body}
    </AuthShell>
  );
}
