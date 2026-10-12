import type { Metadata } from "next";
import { Providers } from "@/components/Providers";
import "./globals.css";
import "./waterfall.css";
import "./search.css";
import "./compare.css";

export const metadata: Metadata = {
  title: "Agent Black Box",
  description: "Flight recorder and observability for AI agents",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
