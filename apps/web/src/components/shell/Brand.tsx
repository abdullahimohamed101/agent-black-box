import Link from "next/link";

/** Placeholder glyph (a recorder-orange rectangle with two bars) and the wordmark; a designed mark is a follow-up. */
export function Brand({ href = "/" }: { href?: string }) {
  return (
    <Link href={href} className="brand">
      <span className="mark" aria-hidden="true" />
      Agent Black Box
    </Link>
  );
}
