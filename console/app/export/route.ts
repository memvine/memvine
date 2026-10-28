import { NextResponse, type NextRequest } from "next/server";
import { forwardedFrom } from "@/lib/forwarded";

// Downloads a project's export (POST /v1/export) with the signed-in session, as a file. Owners
// and admins only; the server records it in the audit log.
export async function GET(request: NextRequest) {
  const project = request.nextUrl.searchParams.get("project") ?? "";
  const token = process.env.MEMVINE_MANAGEMENT_KEY || request.cookies.get("mv_key")?.value || "";
  const base = (process.env.MEMVINE_API_URL ?? "").replace(/\/$/, "");
  if (!base || !project || !token) return new NextResponse("Not available", { status: 400 });
  const res = await fetch(`${base}/v1/export`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "X-Memvine-Project": project, ...forwardedFrom(request.headers) },
    cache: "no-store",
  });
  if (!res.ok) {
    let message = res.statusText;
    try { message = (await res.json()).error?.message ?? message; } catch { /* non-JSON */ }
    return new NextResponse(`Export failed: ${message}`, { status: res.status, headers: { "Content-Type": "text/plain; charset=utf-8" } });
  }
  return new NextResponse(res.body, {
    headers: {
      "Content-Type": "application/json",
      "Content-Disposition": res.headers.get("content-disposition") ?? `attachment; filename="memvine-${project}.json"`,
    },
  });
}
