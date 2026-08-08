import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  devIndicators: false,
  // Uploads de vidéos jusqu'à 5 GB — même limite que le backend FastAPI. Sans ça,
  // Next tronque à 10 MB par défaut et le proxy vers /api/clips/upload casse.
  experimental: {
    proxyClientMaxBodySize: "5gb" as unknown as number,
  },
  // Next 15+ bloque les requêtes dev depuis d'autres hosts que localhost pour
  // éviter le DNS-rebinding. On whitelist explicitement le tailnet + éventuellement
  // d'autres URLs (funnel, IP locale, etc.).
  allowedDevOrigins: [
    "macmini.tailef3707.ts.net",
    "*.tailef3707.ts.net",
    "100.102.28.112",
    "localhost",
  ],
  // Proxy /api/* + /uploads/* + /proxies/* + /outputs/* vers le backend FastAPI :8001
  // Same-origin depuis le frontend → pas de CORS, pas besoin d'exposer :8001 séparément.
  async rewrites() {
    return [
      { source: "/api/:path*",     destination: "http://localhost:8001/api/:path*" },
      { source: "/uploads/:path*", destination: "http://localhost:8001/uploads/:path*" },
      { source: "/proxies/:path*", destination: "http://localhost:8001/proxies/:path*" },
      { source: "/outputs/:path*", destination: "http://localhost:8001/outputs/:path*" },
      { source: "/temp/:path*",    destination: "http://localhost:8001/temp/:path*" },
    ];
  },
};

export default nextConfig;
