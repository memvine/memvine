import { NextResponse, type NextRequest } from "next/server";

// A full navigation (plain link), so the sign-in page renders without the app shell. The redirect
// is relative: behind a proxy or inside a container the server does not know the browser's host,
// and the browser resolves "/login" against the address it used. A person's sign-in session is
// also ended on the server, so a copied cookie stops working.
export async function GET(request: NextRequest) {
  const token = request.cookies.get("mv_key")?.value ?? "";
  const base = (process.env.MEMVINE_API_URL ?? "").replace(/\/$/, "");
  if (token.startsWith("mv_us_") && base) {
    await fetch(`${base}/v1/auth/logout`, { method: "POST", headers: { Authorization: `Bearer ${token}` } }).catch(() => undefined);
  }
  const response = new NextResponse(null, { status: 303, headers: { Location: "/login" } });
  response.cookies.delete("mv_key");
  response.cookies.delete("mv_project");
  return response;
}
