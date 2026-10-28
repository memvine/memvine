import type { NextConfig } from "next";

const https = process.env.NODE_ENV === "production" && !process.env.MEMVINE_INSECURE_COOKIES;

// Origins (host[:port]) besides the request's own Host that may post server actions: needed only
// when a proxy rewrites Host (Railway keeps it, so app.<domain> and *.up.railway.app both work).
const allowedOrigins = (process.env.MEMVINE_CONSOLE_ALLOWED_ORIGINS ?? "")
  .split(",").map((s) => s.trim()).filter(Boolean);

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // A production build beside a running `next dev` needs its own directory (deploy/laptop).
  distDir: process.env.NEXT_DIST_DIR || ".next",
  // The dev badge sits over the sidebar's account block.
  devIndicators: false,
  ...(allowedOrigins.length ? { experimental: { serverActions: { allowedOrigins } } } : {}),
  async headers() {
    // Every response, static assets included; the Content-Security-Policy (per-request nonce) is
    // set in middleware.ts.
    return [{
      source: "/:path*",
      headers: [
        { key: "X-Frame-Options", value: "DENY" },
        { key: "X-Content-Type-Options", value: "nosniff" },
        { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
        { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=()" },
        { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
        ...(https ? [{ key: "Strict-Transport-Security", value: "max-age=31536000; includeSubDomains" }] : []),
      ],
    }];
  },
};

export default nextConfig;
