/** The pulse glyph (brand/pulse-mark.svg, the single source). The cut is the brand orange; the rest is the text colour. */
export function Mark({ size = 24 }: { size?: number }) {
  return (
    <svg className="mark" viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      <path d="M2 13h3.5l2.5-7 3.5 12 2.5-5" />
      <path d="M18 13h4" />
      <path className="cut" d="M15.5 19.5l1-13" strokeWidth="1.5" />
    </svg>
  );
}
