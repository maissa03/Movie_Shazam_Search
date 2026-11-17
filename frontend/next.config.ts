import type { NextConfig } from "next";

// Application 100 % locale : le navigateur parle directement à l'API FastAPI sur 127.0.0.1:8000.
const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
};

export default nextConfig;
