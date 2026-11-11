import type { NextConfig } from "next";

// Static export: the UI is plain HTML/JS that can be served by the FastAPI backend
// (single container) or any static host. Set NEXT_PUBLIC_API_BASE when the API lives elsewhere.
const nextConfig: NextConfig = {
  output: "export",
  images: { unoptimized: true },
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
