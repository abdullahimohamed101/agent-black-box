import { plain, type Line } from "../lib/code";
import { CopyButton } from "./CopyButton";

/** A labelled code block with a Copy control. Server component: the lines are in the HTML; only the button hydrates. */
export function CommandBlock({ id, label, lines, className }: { id: string; label: string; lines: Line[]; className?: string }) {
  return (
    <figure className={`code${className ? ` ${className}` : ""}`}>
      <div className="code-head">
        <span>{label}</span>
        <CopyButton text={plain(lines)} targetId={id} />
      </div>
      <pre id={id} tabIndex={0} aria-label={label}>
        <code>
          {lines.map((line, i) => (
            <span key={i} className="ln">
              {line.map((seg, j) => (typeof seg === "string" ? seg : <span key={j} className={seg[1]}>{seg[0]}</span>))}
              {"\n"}
            </span>
          ))}
        </code>
      </pre>
    </figure>
  );
}
