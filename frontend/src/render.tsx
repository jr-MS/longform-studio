import { useEffect, useState } from "react";
import { api } from "./api";
import { Artboard } from "./Artboard";
import type { Project } from "./generated";

export function RenderPage() {
  const [project, setProject] = useState<Project | null>(null);
  const [error, setError] = useState("");
  const [, , id, sid] = location.pathname.split("/");
  const params = new URLSearchParams(location.search);
  const version = params.get("version");
  useEffect(() => {
    let active = true;
    api<Project>(`/projects/${id}${version ? `?version=${encodeURIComponent(version)}` : ""}`)
      .then(p => { if (active) setProject(p); })
      .catch(e => { if (active) setError(String(e)); });
    return () => { active = false; };
  }, [id, version]);
  const screen = project?.screens.find(s => s.id === sid);
  if (error) return <p role="alert">{error}</p>;
  if (!project || !screen) return <p>正在载入画面…</p>;
  return <Artboard project={project} screen={screen} watermark={params.get("mode") === "draft"} />;
}
