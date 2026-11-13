import type { Project } from "./generated";

export async function api<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method,
    headers: body instanceof FormData ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : body instanceof FormData ? body : JSON.stringify(body),
  });
  const result: unknown = await response.json();
  if (!response.ok) {
    const data = result as { error?: string; detail?: unknown };
    throw new Error(data.error ?? (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail ?? result)));
  }
  return result as T;
}

export const fileUrl = (p: Pick<Project, "id">, file: string, preview?: number) =>
  `/api/projects/${p.id}/files/${encodeURIComponent(file)}${preview ? `?preview=${preview}` : ""}`;

export function downloadName(name: string, ext: string) {
  const cleaned = name.replace(/[\\/:*?"<>|]/g, "-").trim() || "详情长图";
  return `${cleaned}-详情长图.${ext}`;
}

export async function downloadNamed(url: string, filename: string) {
  const response = await fetch(url);
  if (!response.ok) throw new Error("长图下载失败，请在生成记录里再试一次");
  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(objectUrl);
}

export function clampPosition(value: number, extent: number) {
  return Math.max(0, Math.min(1 - extent, value));
}
