import type { Scene } from "../types";

export type LineKind = "heading" | "action" | "cue" | "dialogue" | "paren" | "transition" | "blank";
export interface Line {
  kind: LineKind;
  text: string;
  scene?: number; // set on heading lines
}

const TRANSITION = /^(FADE (IN|OUT|TO BLACK)[.:]?|.*\bCUT TO:?|.*\bDISSOLVE TO:?|[A-Z ]+ TO:)$/;
const SLUG = /^(\d+[A-Z]?\.?\s+)?(INT\.?\/EXT\.?|INT\.?|EXT\.?|EST\.?|I\/E\.?)\s+\S/i;
const isCaps = (s: string) => /[A-Z]/.test(s) && s === s.toUpperCase();

/** Classify screenplay lines for display. Scene headings come from the
 *  server's scene offsets so the outline and the page always agree. */
export function formatScreenplay(content: string, scenes: Scene[]): Line[] {
  const raw = content.split("\n");
  const headingAt = new Map<number, number>();
  const real = scenes.filter((s) => !(scenes.length === 1 && s.heading === "Full script"));
  let offset = 0;
  let si = 0;
  const starts: number[] = [];
  for (const line of raw) {
    starts.push(offset);
    offset += line.length + 1;
  }
  raw.forEach((line, i) => {
    while (si < real.length && real[si].start < starts[i]) si++;
    // the heading is the first non-empty line at/after the scene's start offset
    if (si < real.length && line.trim() && real[si].start <= starts[i] + line.length) {
      headingAt.set(i, real[si].number);
      si++;
    }
  });

  const out: Line[] = [];
  let inDialogue = false;
  raw.forEach((line, i) => {
    const text = line.trim();
    if (!text) {
      inDialogue = false;
      out.push({ kind: "blank", text: "" });
      return;
    }
    if (scenes.length === 0 && SLUG.test(text) && (isCaps(text) || /^(\d+\S*\s+)?(INT|EXT|EST|I\/E)\./i.test(text))) {
      inDialogue = false;
      out.push({ kind: "heading", text });
      return;
    }
    if (headingAt.has(i)) {
      inDialogue = false;
      out.push({ kind: "heading", text, scene: headingAt.get(i) });
      return;
    }
    if (inDialogue) {
      out.push({ kind: text.startsWith("(") ? "paren" : "dialogue", text });
      return;
    }
    if (isCaps(text) && TRANSITION.test(text)) {
      out.push({ kind: "transition", text });
      return;
    }
    const next = raw[i + 1]?.trim();
    const prevBlank = i === 0 || !raw[i - 1].trim();
    if (prevBlank && next && isCaps(text.replace(/\(.*?\)/g, "")) && text.length <= 40 && !/[.!?:]$/.test(text)) {
      inDialogue = true;
      out.push({ kind: "cue", text });
      return;
    }
    out.push({ kind: "action", text });
  });
  return out;
}
