import Link from "next/link";
import { run } from "@/copy/run";
import "../app/title-line.css";

/**
 * The title line of a page that sits under Runs (KI-276): "Runs / name", the back link first. The h1 is the page's name;
 * `children` sit at the right end (status and age on a run). At 375 px the back link is its own small line above the title.
 */
export function TitleLine({
  href,
  title,
  back = run.back,
  children,
}: {
  href: string;
  title: string;
  /** The first level's visible label and accessible name; Runs by default, Settings on a settings tab. */
  back?: { label: string; name: string };
  children?: React.ReactNode;
}) {
  return (
    <div className="title-line">
      <Link className="title-back" href={href} aria-label={back.name}>
        <span className="title-chev" aria-hidden="true">
          ‹
        </span>
        {back.label}
      </Link>
      <span className="title-sep" aria-hidden="true">
        /
      </span>
      <h1>{title}</h1>
      {children}
    </div>
  );
}
