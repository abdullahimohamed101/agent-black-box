import type { EventOut } from "@/lib/api/types";

/**
 * Command text, file paths and similar content are withheld by the API from roles without `payload.read` (ADR-061).
 * The API replaces the value and lists the key in `withheld_attributes`; the UI only chooses the words. Text only.
 */
export const WITHHELD_TEXT = "content withheld for your role";

export const isWithheld = (e: EventOut, key: string): boolean =>
  e.withheld_attributes?.includes(key) === true;

/** A content attribute as display text: the withheld notice when the API withheld it, else the string (or null). */
export const contentText = (e: EventOut, key: string): string | null => {
  if (isWithheld(e, key)) return WITHHELD_TEXT;
  const v = e.attributes[key];
  return typeof v === "string" && v ? v : null;
};
