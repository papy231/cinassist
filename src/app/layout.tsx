import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "HAW CineAssist",
  description: "KI-gestützte Videoschnitt-Plattform · 100% lokal · Open Source",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="de" className="h-full">
      <body className="h-full antialiased">{children}</body>
    </html>
  );
}
