import type { CSSProperties } from "react";
import type { Layer, TextRun } from "./generated";

type Ink = Pick<Layer, "color" | "gradient_end" | "gradient_angle">;

export function inkStyle(ink: Ink): CSSProperties {
  return ink.gradient_end ? {
    backgroundImage: `linear-gradient(${ink.gradient_angle ?? 180}deg, ${ink.color}, ${ink.gradient_end})`,
    backgroundClip: "text", WebkitBackgroundClip: "text", color: "transparent",
    WebkitTextFillColor: "transparent", boxDecorationBreak: "clone",
  } : { color: ink.color };
}

// Match unchanged characters; replacement spans inherit the colors they replace.
export function editText(layer: Layer, content: string): Partial<Layer> {
  if (content === layer.content) return { content, runs: layer.runs };
  if (!layer.runs.length || !content) return { content, runs: [] };
  const before = Array.from(layer.content), after = Array.from(content);
  const inks = layer.runs.flatMap(run => Array.from(run.text, () => run));
  const rows = before.length + 1, cols = after.length + 1;
  const lengths = new Uint16Array(rows * cols);
  for (let i = before.length - 1; i >= 0; i--) {
    for (let j = after.length - 1; j >= 0; j--) {
      lengths[i * cols + j] = before[i] === after[j]
        ? lengths[(i + 1) * cols + j + 1] + 1
        : Math.max(lengths[(i + 1) * cols + j], lengths[i * cols + j + 1]);
    }
  }
  const matches: [number, number][] = [];
  let i = 0, j = 0;
  while (i < before.length && j < after.length) {
    if (before[i] === after[j]) { matches.push([i++, j++]); }
    else if (lengths[(i + 1) * cols + j] >= lengths[i * cols + j + 1]) i++;
    else j++;
  }
  matches.push([before.length, after.length]);
  const runs: TextRun[] = [];
  const append = (text: string, ink: Ink) => {
    const previous = runs[runs.length - 1];
    if (previous && previous.color === ink.color && previous.gradient_end === ink.gradient_end
        && previous.gradient_angle === ink.gradient_angle) previous.text += text;
    else runs.push({ text, color: ink.color, gradient_end: ink.gradient_end ?? null, gradient_angle: ink.gradient_angle ?? 180 });
  };
  let oldStart = 0, newStart = 0;
  for (const [oldEnd, newEnd] of matches) {
    for (let k = newStart; k < newEnd; k++) {
      const source = oldEnd > oldStart
        ? oldStart + Math.min(oldEnd - oldStart - 1, Math.floor((k - newStart) * (oldEnd - oldStart) / (newEnd - newStart)))
        : Math.max(0, oldStart - 1);
      append(after[k], inks[source] ?? layer);
    }
    if (newEnd < after.length) append(after[newEnd], inks[oldEnd] ?? layer);
    oldStart = oldEnd + 1; newStart = newEnd + 1;
  }
  return { content, runs };
}
