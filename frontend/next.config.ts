import type { NextConfig } from "next";

// The browser only talks to this origin; /api/* is proxied to the Python API.
// Same-origin requests keep the session cookie first-party (SameSite=Lax).
const backend = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

const config: NextConfig = {
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/:path*` }];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Frame-Options", value: "DENY" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
        ],
      },
    ];
  },
};

export default config;
