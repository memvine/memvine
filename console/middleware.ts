import { NextResponse, type NextRequest } from "next/server";

// In live mode with no key configured on the console server, every page needs a signed-in
// organization: no cookie, no console. The key itself is checked against the API at sign-in.
export function middleware(request: NextRequest) {
  const forward = new Headers(request.headers);
  const live = Boolean(process.env.MEMVINE_API_URL);
  const configured = Boolean(process.env.MEMVINE_MANAGEMENT_KEY);
  if (request.nextUrl.pathname === "/login" || request.nextUrl.pathname === "/signup") {
    forward.set("x-memvine-login", "1");
    return NextResponse.next({ request: { headers: forward } });
  }
  if (!live || configured || request.cookies.get("mv_key")) return NextResponse.next({ request: { headers: forward } });
  // Serve the sign-in page in place rather than redirecting: behind a tunnel or proxy the console
  // only knows its local address (localhost:3400), and an absolute redirect would send the
  // browser there (middleware refuses relative ones). Signing in then loads "/".
  forward.set("x-memvine-login", "1");
  const url = request.nextUrl.clone();
  url.pathname = "/login";
  url.search = "";
  // A proxy's X-Forwarded-Proto: https makes nextUrl https, while this server speaks http; a
  // rewrite to another scheme is proxied out as an external request (and fails TLS).
  url.protocol = "http:";
  return NextResponse.rewrite(url, { request: { headers: forward } });
}

export const config = {
  matcher: ["/((?!_next|icon.svg|favicon.ico).*)"],
};
