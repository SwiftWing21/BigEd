import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      {
        source: "/api/fleet/:path*",
        destination: "http://localhost:7777/api/fleet/:path*",
      },
      {
        source: "/api/mcp/:path*",
        destination: "http://localhost:7777/api/mcp/:path*",
      },
      {
        source: "/api/agents/:path*",
        destination: "http://localhost:7777/api/agents/:path*",
      },
      {
        source: "/api/audit/:path*",
        destination: "http://localhost:7777/api/audit/:path*",
      },
      {
        source: "/api/sla",
        destination: "http://localhost:7777/api/sla",
      },
      {
        source: "/api/web/:path*",
        destination: "http://localhost:7777/api/web/:path*",
      },
    ];
  },
};

export default nextConfig;
