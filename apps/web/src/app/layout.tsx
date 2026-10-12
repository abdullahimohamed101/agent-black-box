import type { Metadata } from "next";
import localFont from "next/font/local";
import { Providers } from "@/components/Providers";
import "./globals.css";
import "./waterfall.css";
import "./search.css";
import "./compare.css";

// IBM Plex (SIL OFL 1.1, see src/fonts/OFL.txt), vendored and served from our own origin: an observability tool makes no
// third-party request for a typeface (ADR-110). Latin-1 subsets; anything else falls back through the CSS stacks.
const IBMPlexSans = localFont({
  src: [
    { path: "../fonts/IBMPlexSans-Regular-Latin1.woff2", weight: "400", style: "normal" },
    { path: "../fonts/IBMPlexSans-Medium-Latin1.woff2", weight: "500", style: "normal" },
    { path: "../fonts/IBMPlexSans-SemiBold-Latin1.woff2", weight: "600", style: "normal" },
  ],
  variable: "--font-plex-sans",
  display: "swap",
});
const IBMPlexSansCondensed = localFont({
  src: [
    { path: "../fonts/IBMPlexSansCondensed-Medium-Latin1.woff2", weight: "500", style: "normal" },
    { path: "../fonts/IBMPlexSansCondensed-SemiBold-Latin1.woff2", weight: "600", style: "normal" },
  ],
  variable: "--font-plex-cond",
  display: "swap",
});
const IBMPlexMono = localFont({
  src: [
    { path: "../fonts/IBMPlexMono-Regular-Latin1.woff2", weight: "400", style: "normal" },
    { path: "../fonts/IBMPlexMono-Medium-Latin1.woff2", weight: "500", style: "normal" },
  ],
  variable: "--font-plex-mono",
  display: "swap",
});

// Serif Regular and Medium: used only for the failure line and empty-state headlines (ADR-111).
const IBMPlexSerif = localFont({
  src: [
    { path: "../fonts/IBMPlexSerif-Regular-Latin1.woff2", weight: "400", style: "normal" },
    { path: "../fonts/IBMPlexSerif-Medium-Latin1.woff2", weight: "500", style: "normal" },
  ],
  variable: "--font-plex-serif",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Agent Black Box",
  description: "Flight recorder and observability for AI agents",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body
        className={`${IBMPlexSans.variable} ${IBMPlexSansCondensed.variable} ${IBMPlexMono.variable} ${IBMPlexSerif.variable}`}
      >
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
