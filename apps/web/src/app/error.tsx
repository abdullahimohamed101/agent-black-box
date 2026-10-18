"use client";

import Link from "next/link";
import { Brand } from "@/components/shell/Brand";
import { auth } from "@/copy/auth";
import "./login.css";

// The error boundary. It never prints the error's message (it can carry request details); the digest is the handle for the
// server log. No drawing: routine failures are a sentence (ADR-166).
export default function ErrorPage({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <main id="main" className="state-page" data-state="error">
      <Brand />
      <h1>{auth.error.title}</h1>
      <p className="muted" role="alert">
        {auth.error.body(error.digest)}
      </p>
      <p className="state-actions">
        <button type="button" onClick={reset}>
          {auth.error.retry}
        </button>
        <Link href="/">{auth.error.action}</Link>
      </p>
    </main>
  );
}
