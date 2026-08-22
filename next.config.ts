import type { NextConfig } from "next";

// Ziel-Backend. Die Voreinstellung ist die bisherige Instanz auf Port 8001.
// Über CINASSIST_BACKEND_URL lässt sich eine zweite, getrennte Instanz ansteuern,
// etwa ein weiteres Projekt mit eigener Datenbank und eigenem Medienordner.
const BACKEND = process.env.CINASSIST_BACKEND_URL ?? "http://localhost:8001";

const nextConfig: NextConfig = {
  devIndicators: false,
  // Video-Uploads bis 5 GB, dieselbe Grenze wie im FastAPI-Backend. Ohne diese
  // Angabe kappt Next bei 10 MB und der Weiterreichung an /api/clips/upload bricht ab.
  experimental: {
    proxyClientMaxBodySize: "5gb" as unknown as number,
  },
  // Ab Next 15 werden Anfragen aus der Entwicklungsumgebung nur von localhost
  // angenommen, um DNS-Rebinding zu verhindern. Das Tailnet und weitere Adressen
  // werden hier ausdrücklich zugelassen.
  allowedDevOrigins: [
    "macmini.tailef3707.ts.net",
    "*.tailef3707.ts.net",
    "100.102.28.112",
    "localhost",
  ],
  // /api, /uploads, /proxies, /outputs und /temp werden an das Backend weitergereicht.
  // Aus Sicht des Browsers liegt damit alles auf derselben Herkunft, es entfällt
  // sowohl CORS als auch die Notwendigkeit, den Backend-Port getrennt zu öffnen.
  async rewrites() {
    return [
      { source: "/api/:path*",     destination: `${BACKEND}/api/:path*` },
      { source: "/uploads/:path*", destination: `${BACKEND}/uploads/:path*` },
      { source: "/proxies/:path*", destination: `${BACKEND}/proxies/:path*` },
      { source: "/outputs/:path*", destination: `${BACKEND}/outputs/:path*` },
      { source: "/temp/:path*",    destination: `${BACKEND}/temp/:path*` },
    ];
  },
};

export default nextConfig;
