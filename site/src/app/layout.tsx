import type { ReactNode } from "react";
import localFont from "next/font/local";
// Token file first: site.css and every later stylesheet read the custom properties it defines (generated from
// apps/web/design-tokens/tokens.json by scripts/tokens.mjs).
import "../styles/tokens.css";
import "../styles/site.css";

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

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={`${IBMPlexSans.variable} ${IBMPlexMono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
