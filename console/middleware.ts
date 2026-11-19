import { NextResponse, type NextRequest } from "next/server";

const DEV = process.env.NODE_ENV !== "production";
// Plain-HTTP deployments (a container on a laptop) opt out of HTTPS-only settings.
const HTTPS = !DEV && !process.env.MEMVINE_INSECURE_COOKIES;

/** A Content-Security-Policy with a per-request nonce: Next.js reads the nonce from this header on
 *  the request and puts it on every script it renders, so no other script runs. Styles keep
 *  'unsafe-inline' (React style attributes); nothing loads from another origin. */
function contentSecurityPolicy(nonce: string): string {
  return [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${DEV ? " 'unsafe-eval'" : ""}`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    `connect-src 'self'${DEV ? " ws: wss:" : ""}`,
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
    ...(HTTPS ? ["upgrade-insecure-requests"] : []),
  ].join("; ");
}

// In live mode with no key configured on the console server, every page needs a signed-in
// organization: no cookie, no console. The key itself is checked against the API at sign-in.
export function middleware(request: NextRequest) {
  const path = request.nextUrl.pathname;
  // Plain GET routes that act with the session cookie (a SameSite=Lax cookie rides along on a
  // link from another site): only from the console's own pages.
  if ((path === "/export" || path === "/logout") && request.headers.get("sec-fetch-site") === "cross-site") {
    return new NextResponse("Open this from the console.", { status: 403 });
  }
  const nonce = btoa(crypto.randomUUID());
  const csp = contentSecurityPolicy(nonce);
  const forward = new Headers(request.headers);
  forward.set("content-security-policy", csp);
  forward.set("x-nonce", nonce);
  const secured = (response: NextResponse) => {
    response.headers.set("Content-Security-Policy", csp);
    return response;
  };
  const live = Boolean(process.env.MEMVINE_API_URL);
  const configured = Boolean(process.env.MEMVINE_MANAGEMENT_KEY);
  if (path === "/login" || path === "/signup" || path === "/reset") {
    forward.set("x-memvine-login", "1");
    return secured(NextResponse.next({ request: { headers: forward } }));
  }
  if (!live || configured || request.cookies.get("mv_key")) return secured(NextResponse.next({ request: { headers: forward } }));
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
  return secured(NextResponse.rewrite(url, { request: { headers: forward } }));
}

export const config = {
  matcher: ["/((?!_next|icon.svg|favicon.ico).*)"],
};
