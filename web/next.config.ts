import type { NextConfig } from "next";

// One origin for the phone: the browser calls /api/* here and Next forwards it to the FastAPI backend.
const nextConfig: NextConfig = {
  compress: false, // gzip would buffer the server-sent event streams
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  async rewrites() {
    return [{ source: "/api/:path*", destination: "http://127.0.0.1:8000/api/:path*" }];
  },
};

export default nextConfig;
