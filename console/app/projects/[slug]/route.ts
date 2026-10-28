// /projects/<slug>: a shareable link to a project. Selects it (the same cookie the sidebar's
// project switcher sets) and opens its Overview, or the console path in ?to=, e.g.
// /projects/muse?to=/sources. An unknown slug lands on the Projects list, which says so.
import { NextResponse, type NextRequest } from "next/server";
import { listProjects, PROJECT_COOKIE } from "@/lib/api";

/** Only a path inside the console: never another site (//host or a full URL). */
function inside(to: string | null): string {
  return to && to.startsWith("/") && !to.startsWith("//") && !to.startsWith("/\\") ? to : "/";
}

export async function GET(request: NextRequest, { params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const found = (await listProjects()).some((p) => p.slug === slug);
  // A relative Location: behind a tunnel or proxy the console only knows its local address.
  const location = found ? inside(request.nextUrl.searchParams.get("to")) : `/projects?missing=${encodeURIComponent(slug)}`;
  const response = new NextResponse(null, { status: 303, headers: { Location: location } });
  if (found) response.cookies.set(PROJECT_COOKIE, slug, { path: "/", sameSite: "lax" });
  return response;
}
