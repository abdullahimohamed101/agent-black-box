import Link from "next/link";
import { shell } from "@/copy/shell";

/** The flight-recorder mark (docs/design/brand-mark-options.md, option A): a trace line with one cut where the run broke. */
export function BrandMark() {
  return (
    <svg
      className="brand-mark"
      viewBox="0 0 24 24"
      width="24"
      height="24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <path d="M2 13h3.5l2.5-7 3.5 12 2.5-5" />
      <path d="M18 13h4" />
      <path d="M15.5 19.5l1-13" strokeWidth="1.5" />
    </svg>
  );
}

/** The name gives way to the mark alone on a phone (CSS); the link keeps its name for assistive technology. */
export function Brand({ href = "/" }: { href?: string }) {
  return (
    <Link href={href} className="brand">
      <BrandMark />
      <span className="brand-name">{shell.brand}</span>
    </Link>
  );
}
