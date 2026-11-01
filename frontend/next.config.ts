import type { NextConfig } from "next";

/** Where the Next.js server proxies `/api/*`: the Quack backend (FastAPI) */
const API_URL = process.env.QUACK_API_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  devIndicators: false,
  // gzip buffers proxied SSE (teacher tokens, learner stream) until the response ends
  compress: false,
  cacheComponents: true,
  partialPrefetching: true,
  // The browser talks to the backend through this proxy, so the backend needs no CORS
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_URL}/:path*` }];
  },
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
