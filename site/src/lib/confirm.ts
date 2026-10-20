/**
 * A copy line that waits for a fact is tagged in the export, not in a comment (React does not emit comments):
 * `<p {...confirm("AFTER STEP 12: the screens are pass-2 stand-ins")}>`. `scripts/check-site.sh --release` fails while any
 * `[CONFIRM ...]` marker is in the output, and lists them. Remove the call when the fact is verified.
 */
export const confirm = (what: string) => ({ "data-confirm": `[CONFIRM ${what}]` }) as const;
