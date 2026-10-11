/** A table wider than a phone scrolls inside its own region; the region is focusable so a keyboard can scroll it. */
export function TableScroll({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="table-scroll" tabIndex={0} role="region" aria-label={label}>
      {children}
    </div>
  );
}
