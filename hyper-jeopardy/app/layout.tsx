import type { Metadata } from "next";
import localFont from "next/font/local";
import "./globals.css";
import Starfield from "@/components/Starfield";

// Self-hosted so the production build never depends on Google Fonts network
// access. Orbitron carries the "space-age display" role (title / values /
// category names); Exo 2 is the readable body + headline face. We keep the
// legacy CSS variable names (--font-anton / --font-oswald / --font-inter) so
// every existing class and JSX reference re-themes without edits.
const orbitron = localFont({
  variable: "--font-anton",
  src: [
    { path: "./fonts/Orbitron-500.woff2", weight: "500", style: "normal" },
    { path: "./fonts/Orbitron-700.woff2", weight: "700", style: "normal" },
    { path: "./fonts/Orbitron-900.woff2", weight: "900", style: "normal" },
  ],
});

const exoHeadline = localFont({
  variable: "--font-oswald",
  src: [{ path: "./fonts/Exo2-600.woff2", weight: "600", style: "normal" }],
});

const exo = localFont({
  variable: "--font-inter",
  src: [
    { path: "./fonts/Exo2-400.woff2", weight: "400", style: "normal" },
    { path: "./fonts/Exo2-600.woff2", weight: "600", style: "normal" },
  ],
});

export const metadata: Metadata = {
  title: "Hyper Jeopardy",
  description: "Multiplayer Hyper Jeopardy — a Central Industrial game",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${orbitron.variable} ${exoHeadline.variable} ${exo.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">
        {/* Ambient space backdrop behind every route (display + phones) */}
        <Starfield />
        {children}
      </body>
    </html>
  );
}
