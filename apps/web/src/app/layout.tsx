import type { Metadata } from "next";
import localFont from "next/font/local";
import { Providers } from "@/components/Providers";
// Token files first: globals.css and every later stylesheet read the custom properties they define (generated from
// design-tokens/tokens.json; `pnpm tokens:build`).
import "./tokens.generated.css";
import "./tokens.aliases.css";
import "./globals.css";
import "./waterfall.css";
import "./search.css";
import "./compare.css";

// IBM Plex (SIL OFL 1.1, see src/fonts/OFL.txt), vendored and served from our own origin: an observability tool makes no
// third-party request for a typeface (ADR-110). Latin-1 subsets; anything else falls back through the CSS stacks.
// ADR-166: the product is Plex Sans and Plex Mono only. Sans and Mono are preloaded; Condensed and Serif are DEPRECATED:
// still declared (screens read them until step 5 rebuilds those screens and deletes the faces and files) but no longer
// preloaded, so the browser fetches them only if something on the page renders in them. next/font preloads per family,
// not per file, so Sans keeps its Medium file in the preload set until step 5 removes the 500 weight.
const IBMPlexSans = localFont({
  src: [
    { path: "../fonts/IBMPlexSans-Regular-Latin1.woff2", weight: "400", style: "normal" },
    { path: "../fonts/IBMPlexSans-Medium-Latin1.woff2", weight: "500", style: "normal" },
    { path: "../fonts/IBMPlexSans-SemiBold-Latin1.woff2", weight: "600", style: "normal" },
  ],
  variable: "--font-plex-sans",
  display: "swap",
});
// DEPRECATED (ADR-166): headings, labels and the tab bar until step 5.
const IBMPlexSansCondensed = localFont({
  src: [
    { path: "../fonts/IBMPlexSansCondensed-Medium-Latin1.woff2", weight: "500", style: "normal" },
    { path: "../fonts/IBMPlexSansCondensed-SemiBold-Latin1.woff2", weight: "600", style: "normal" },
  ],
  variable: "--font-plex-cond",
  display: "swap",
  preload: false, // deprecated (ADR-166)
});
const IBMPlexMono = localFont({
  src: [
    { path: "../fonts/IBMPlexMono-Regular-Latin1.woff2", weight: "400", style: "normal" },
    { path: "../fonts/IBMPlexMono-Medium-Latin1.woff2", weight: "500", style: "normal" },
  ],
  variable: "--font-plex-mono",
  display: "swap",
});

// DEPRECATED (ADR-166). Serif Regular and Medium: used only for the failure line and empty-state headlines (ADR-111).
const IBMPlexSerif = localFont({
  src: [
    { path: "../fonts/IBMPlexSerif-Regular-Latin1.woff2", weight: "400", style: "normal" },
    { path: "../fonts/IBMPlexSerif-Medium-Latin1.woff2", weight: "500", style: "normal" },
  ],
  variable: "--font-plex-serif",
  display: "swap",
  preload: false, // deprecated (ADR-166)
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
      <body
        className={`${IBMPlexSans.variable} ${IBMPlexSansCondensed.variable} ${IBMPlexMono.variable} ${IBMPlexSerif.variable}`}
      >
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
