import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import localFont from "next/font/local";
import { RELEASE_CLAIMS, REPO_URL, publicUrl, tokenValue } from "../config";
import { NAME, SUB } from "../content/copy";
// Token file first: site.css and every later stylesheet read the custom properties it defines (generated from
// apps/web/design-tokens/tokens.json by scripts/tokens.mjs).
import "../styles/tokens.css";
import "../styles/site.css";
import "../styles/hero.css";
import "../styles/trace.css";
import "../styles/sections.css";

// IBM Plex (SIL OFL 1.1, public/fonts/OFL.txt), served from our own origin: this page makes no third-party request.
// Three files, the only ones the type scale uses. The variable names are the app's, so the token-generated --font-sans and
// --font-mono resolve exactly as they do in the product (without them the `font` shorthand falls back to serif).
const IBMPlexSans = localFont({
  src: [
    { path: "../fonts/IBMPlexSans-Regular-Latin1.woff2", weight: "400", style: "normal" },
    { path: "../fonts/IBMPlexSans-SemiBold-Latin1.woff2", weight: "600", style: "normal" },
  ],
  variable: "--font-plex-sans",
  display: "swap",
  preload: true,
});
const IBMPlexMono = localFont({
  src: [{ path: "../fonts/IBMPlexMono-Regular-Latin1.woff2", weight: "400", style: "normal" }],
  variable: "--font-plex-mono",
  display: "swap",
  preload: true,
});

// Open Graph and Twitter tags point at the exported card (added with the brand assets); the absolute URL is only known after
// publication, so metadataBase stays unset and the tags are emitted relative to the export.
const PAGES_ORIGIN = `https://${REPO_URL.split("/")[3]}.github.io`;
const CARD = publicUrl("assets/social-card.png");
const CARD_ALT = "Agent BlackBox: a run timeline with a red line at the first error.";

export const metadata: Metadata = {
  title: `${NAME}: a flight recorder for AI agents`,
  description: SUB,
  icons: {
    icon: [
      { url: publicUrl("assets/favicon.svg"), type: "image/svg+xml" },
      { url: publicUrl("assets/favicon-32.png"), sizes: "32x32", type: "image/png" },
    ],
    apple: [{ url: publicUrl("assets/favicon-180.png"), sizes: "180x180", type: "image/png" }],
  },
  // The state of the publication flag, so scripts/check_site_claims.py knows which rules apply to this export.
  other: { "abb-release-claims": RELEASE_CLAIMS ? "1" : "0" },
  manifest: publicUrl("site.webmanifest"),
  // Open Graph needs an absolute card URL. Until the owner says otherwise (ABB_SITE_URL) it is the default GitHub Pages address
  // of the repository owner (D16, no custom domain); the page itself loads nothing from it.
  metadataBase: new URL(process.env.ABB_SITE_URL ?? PAGES_ORIGIN),
  openGraph: { title: NAME, description: SUB, type: "website", images: [{ url: CARD, width: 1200, height: 630, alt: CARD_ALT }] },
  twitter: { card: "summary_large_image", title: NAME, description: SUB, images: [{ url: CARD, alt: CARD_ALT }] },
};

// The page follows the system scheme (ADR-166: no theme switch); two theme-color metas cover both.
export const viewport: Viewport = {
  colorScheme: "light dark",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: tokenValue("--color-bg-canvas", "light") },
    { media: "(prefers-color-scheme: dark)", color: tokenValue("--color-bg-canvas", "dark") },
  ],
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={`${IBMPlexSans.variable} ${IBMPlexMono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
