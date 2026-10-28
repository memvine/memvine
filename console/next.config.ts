import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // A production build beside a running `next dev` needs its own directory (deploy/laptop).
  distDir: process.env.NEXT_DIST_DIR || ".next",
  // The dev badge sits over the sidebar's account block.
  devIndicators: false,
};

export default nextConfig;
