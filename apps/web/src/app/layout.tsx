import type { Metadata } from "next";
import localFont from "next/font/local";
import { Providers } from "@/components/Providers";
// Token files first: globals.css and every later stylesheet read the custom properties they define (generated from
// design-tokens/tokens.json; `pnpm tokens:build`).
import "./tokens.generated.css";
import "./globals.css";
import "./shell.css";
import "./waterfall.css";
import "./search.css";
import "./compare.css";

// IBM Plex (SIL OFL 1.1, see src/fonts/OFL.txt), vendored and served from our own origin: an observability tool makes no
// third-party request for a typeface (ADR-110). Latin-1 subsets; anything else falls back through the CSS stacks.
// ADR-166: the product is Plex Sans and Plex Mono only, in two weights (400 and 600). Plex Sans Condensed, Plex Serif and
// Plex Sans Medium are gone (batch 5b). Plex Mono has no SemiBold file here, so its Medium file is registered as weight 600:
// a mono word at 600 is drawn from real Medium glyphs rather than a browser's synthetic bold.
const IBMPlexSans = localFont({
  src: [
    { path: "../fonts/IBMPlexSans-Regular-Latin1.woff2", weight: "400", style: "normal" },
    { path: "../fonts/IBMPlexSans-SemiBold-Latin1.woff2", weight: "600", style: "normal" },
  ],
  variable: "--font-plex-sans",
  display: "swap",
});
const IBMPlexMono = localFont({
  src: [
    { path: "../fonts/IBMPlexMono-Regular-Latin1.woff2", weight: "400", style: "normal" },
    { path: "../fonts/IBMPlexMono-Medium-Latin1.woff2", weight: "600", style: "normal" },
  ],
  variable: "--font-plex-mono",
  display: "swap",
});

// Every page is rendered per request: the Content-Security-Policy carries a fresh nonce that Next stamps on its inline
// bootstrap scripts, and a prerendered page has no request to take it from (src/proxy.ts, ADR-155).
export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Agent Black Box",
  description: "Flight recorder and observability for AI agents",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className={`${IBMPlexSans.variable} ${IBMPlexMono.variable}`}>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
