import type { Metadata } from "next";
import { Hanken_Grotesk } from "next/font/google";
import "./globals.css";

const hanken = Hanken_Grotesk({ subsets: ["latin"], weight: ["400", "500", "600", "700"] });

export const metadata: Metadata = {
  title: "CinAssist",
  description: "Interaktiver Videoeditor",
};

// Safari (WebKit) ne rejoue pas systématiquement les credentials Basic Auth sur
// les fetch/XHR subséquents, contrairement à Chrome. Sans ce patch, l'app reste
// bloquée sur "Clips werden geladen…" côté Safari derrière la démo Basic Auth.
const SAFARI_FETCH_CREDS = `
(function(){
  if (typeof window === "undefined" || !window.fetch) return;
  var orig = window.fetch;
  window.fetch = function(input, init){
    init = init || {};
    if (!init.credentials) init.credentials = "include";
    return orig(input, init);
  };
})();
`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="de">
      <head>
        <script dangerouslySetInnerHTML={{ __html: SAFARI_FETCH_CREDS }} />
      </head>
      <body className={hanken.className}>{children}</body>
    </html>
  );
}
