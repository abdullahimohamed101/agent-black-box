import Link from "next/link";
import { RecorderTape } from "@/components/illustrations";
import { Brand } from "@/components/shell/Brand";
import { auth } from "@/copy/auth";
import "./login.css";

// Not-found keeps its small tape drawing (one of the two places an illustration stays, ADR-166).
export default function NotFound() {
  return (
    <main id="main" className="state-page" data-state="not-found">
      <Brand />
      <RecorderTape />
      <h1>{auth.notFound.title}</h1>
      <p className="muted">{auth.notFound.body}</p>
      <p className="state-actions">
        <Link href="/" className="button-link">
          {auth.notFound.action}
        </Link>
      </p>
    </main>
  );
}
