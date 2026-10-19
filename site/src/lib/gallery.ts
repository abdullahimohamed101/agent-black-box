import manifest from "../../screenshots.json";
import generated from "../content/shots.generated.json";
import { publicUrl } from "../config";

export interface Callout { x: number; y: number; w: number; h: number }
export interface GalleryPanel {
  id: string;
  tab: string;
  sub: string;
  zoom: number;
  offsetX: number;
  offsetY: number;
  callouts: Callout[];
  notes: string[];
  caption: string;
  alt: string;
  standIn: boolean;
  light: { webp: string; png: string; w: number; h: number };
  dark: { webp: string; png: string; w: number; h: number };
}

/** Manifest (words, crops, callouts) joined with the generated image facts (files, pixel sizes, stand-in flag). */
export function galleryPanels(): GalleryPanel[] {
  return manifest.panels.map((p) => {
    const g = generated.panels.find((x) => x.id === p.id);
    if (!g || !g.light || !g.dark) throw new Error(`no generated images for panel ${p.id}: run scripts/site-images.mjs`);
    const url = (f: string) => publicUrl(`assets/shots/${f}`);
    const side = (s: { webp: string; png: string; w: number; h: number }) => ({ webp: url(s.webp), png: url(s.png), w: s.w, h: s.h });
    return { id: p.id, tab: p.tab, sub: p.sub, zoom: p.zoom, offsetX: p.offsetX, offsetY: p.offsetY, callouts: p.callouts, notes: p.notes, caption: p.caption, alt: p.alt, standIn: g.standIn, light: side(g.light), dark: side(g.dark) };
  });
}
