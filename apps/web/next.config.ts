import type { NextConfig } from "next";
const nextConfig: NextConfig = {
  output: "export",
  images: { unoptimized: true },
  // The disposable Playwright harness serves the app from this local origin.
  allowedDevOrigins: ["127.0.0.1"],
};
export default nextConfig;
