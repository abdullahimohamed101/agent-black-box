"use client";

import { useEffect, useRef, useState } from "react";
import { useUnsavedGuard } from "@/lib/useUnsavedGuard";
import "../app/alerts.css";

/**
 * A webhook's signing secret, shown once. It lives only in the props of this panel: it is not written to storage, the
 * URL or the query cache, and dismissing the panel drops it. Leaving the page asks first, because it cannot be shown again.
 */
export function SecretOncePanel({
  destinationName,
  secret,
  onDismiss,
}: {
  destinationName: string;
  secret: string;
  onDismiss: () => void;
}) {
  const [stored, setStored] = useState(false);
  const [copied, setCopied] = useState<"idle" | "ok" | "failed">("idle");
  const ref = useRef<HTMLDivElement>(null);
  useUnsavedGuard(true, "The signing secret is shown once. Leave without storing it?");
  useEffect(() => ref.current?.focus(), []);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(secret);
      setCopied("ok");
    } catch {
      setCopied("failed");
    }
  };
  return (
    <div
      ref={ref}
      tabIndex={-1}
      role="region"
      aria-label="Signing secret, shown once"
      className="withheld al-secret"
      data-testid="secret-panel"
    >
      <p>
        Signing secret for <strong>{destinationName}</strong>. It is shown once and is not kept in
        this browser. Store it where the receiver can read it; if it is lost, rotate it for a new
        one.
      </p>
      <p className="secret" data-testid="signing-secret">
        {secret}
      </p>
      <p className="muted">
        Each alert carries <code>X-ABB-Signature: v1=&lt;hex&gt;</code>, an HMAC-SHA256 of the
        timestamp, a dot and the body, keyed with this secret.
      </p>
      <p className="al-actions">
        <button type="button" onClick={() => void copy()}>
          Copy secret
        </button>
        <span role="status" className="muted">
          {copied === "ok" && "Copied."}
          {copied === "failed" && "Could not copy. Select the secret and copy it by hand."}
        </span>
      </p>
      <label className="al-check">
        <input type="checkbox" checked={stored} onChange={(e) => setStored(e.target.checked)} />I
        have stored the secret
      </label>
      <p>
        <button type="button" className="primary" disabled={!stored} onClick={onDismiss}>
          Done
        </button>
      </p>
    </div>
  );
}
