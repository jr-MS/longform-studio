import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { clampPosition, fileUrl } from "./api";
import type { Layer, Project, Screen } from "./generated";
import { inkStyle } from "./textStyle";

type Props = {
  project: Project;
  screen: Screen;
  route?: "B" | "concept";
  text?: boolean;
  selected?: string;
  onSelect?: (id: string) => void;
  onMove?: (id: string, x: number, y: number) => void;
  onTextChange?: (id: string, content: string, restore?: Layer) => void;
  watermark?: boolean;
  emptyLabel?: string;
  preview?: boolean;
};

export function engineeringTextReady(screen: { kind: string; layout_from_concept?: boolean }) {
  return screen.kind === "parameters" || !!screen.layout_from_concept;
}

export function Artboard({ project, screen, route, text = true, selected, onSelect, onMove, onTextChange, watermark, emptyLabel, preview = false }: Props) {
  const board = useRef<HTMLDivElement>(null);
  const drag = useRef<{ id: string; clientX: number; clientY: number; layer: Layer } | null>(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<{ id: string; original: Layer } | null>(null);
  const variant = route === "concept" ? screen.concept : screen.b;
  const url = variant ? fileUrl(project, variant.file, preview ? 960 : undefined) : "";

  useEffect(() => { setEditing(null); }, [url, text, screen.id]);
  useEffect(() => {
    let active = true;
    setReady(false);
    setError("");
    const images = [...(board.current?.querySelectorAll("img") ?? [])];
    if (preview) {
      void document.fonts.ready.then(() => { if (active) setReady(true); });
      return () => { active = false; };
    }
    Promise.all([document.fonts.ready, ...images.map(img => img.decode())])
      .then(() => { if (active) setReady(true); })
      .catch(() => { if (active) setError("图片未能载入，请检查素材文件。"); });
    return () => { active = false; };
  }, [url, preview, screen.height]);

  return <div
    ref={board}
    className="artboard"
    data-artboard
    data-ready={ready && !error ? "true" : "false"}
    style={{ width: project.width, height: screen.height, background: screen.background }}
    onPointerMove={event => {
      if (!drag.current || !board.current || !onMove) return;
      const bounds = board.current.getBoundingClientRect();
      const d = drag.current;
      if (Math.hypot(event.clientX - d.clientX, event.clientY - d.clientY) < 3) return;
      onMove(d.id,
        clampPosition(d.layer.x + (event.clientX - d.clientX) / bounds.width, d.layer.width),
        clampPosition(d.layer.y + (event.clientY - d.clientY) / bounds.height, d.layer.height));
    }}
    onPointerUp={() => { drag.current = null; }}
    onPointerCancel={() => { drag.current = null; }}
  >
    {url && <img className="board-background" src={url} alt="" draggable={false} loading={preview ? "lazy" : "eager"} decoding="async" onError={() => setError("图片未能载入，请检查素材文件。")} />}
    {!url && screen.kind !== "parameters" && <div className={`unrendered${emptyLabel ? " waiting-type" : ""}`}>{emptyLabel ?? (route === "concept" ? "本屏参考稿尚未生成" : "无字底图尚未生成")}</div>}
    {text && route !== "concept" && screen.layers.map(layer => {
      const style: CSSProperties = {
        position: "absolute", left: `${layer.x * 100}%`, top: `${layer.y * 100}%`,
        width: `${layer.width * 100}%`, height: `${layer.height * 100}%`,
        color: layer.color, opacity: layer.opacity,
        fontFamily: `"${layer.font}", sans-serif`, fontSize: layer.font_size,
        fontWeight: layer.weight, lineHeight: layer.line_height, textAlign: layer.align,
        borderRadius: layer.radius,
        background: layer.kind === "text" ? undefined : layer.gradient_end
          ? `linear-gradient(${layer.gradient_angle ?? 180}deg, ${layer.color}, ${layer.gradient_end})` : layer.color,
        cursor: editing?.id === layer.id ? "text" : onMove ? "move" : undefined,
      };
      return <div
        key={layer.id}
        className={`board-layer ${selected === layer.id ? "selected-layer" : ""}`}
        style={style}
        data-text-layer={layer.kind === "text" ? layer.id : undefined}
        role={onSelect ? editing?.id === layer.id ? "group" : "button" : undefined}
        tabIndex={onSelect && editing?.id !== layer.id ? 0 : undefined}
        aria-label={onSelect ? `编辑图层：${layer.content || layer.kind}` : undefined}
        onKeyDown={event => {
          if (event.target !== event.currentTarget || !["Enter", "F2"].includes(event.key)) return;
          event.preventDefault(); event.stopPropagation(); onSelect?.(layer.id);
          if (layer.kind === "text" && onTextChange) setEditing({ id: layer.id, original: layer });
        }}
        onDoubleClick={event => {
          if (layer.kind !== "text" || !onTextChange) return;
          event.stopPropagation();
          drag.current = null;
          onSelect?.(layer.id);
          setEditing({ id: layer.id, original: layer });
        }}
        onPointerDown={event => {
          if (!onSelect || !board.current || event.button !== 0 || editing?.id === layer.id) return;
          event.preventDefault();
          onSelect(layer.id);
          if (!onMove) return;
          event.currentTarget.setPointerCapture(event.pointerId);
          drag.current = { id: layer.id, clientX: event.clientX, clientY: event.clientY, layer };
        }}
      >{layer.kind === "text" ? <><TextInk layer={layer} />{editing?.id === layer.id && onTextChange
        && <InlineText value={layer.content} onChange={content => onTextChange(layer.id, content)}
            onFinish={() => setEditing(null)} onCancel={() => { onTextChange(layer.id, editing.original.content, editing.original); setEditing(null); }} />}</> : null}</div>;
    })}
    {watermark && <div className="draft-watermark">设计草稿 · 数据与画面待核对</div>}
    {error && <div className="board-error">{error}</div>}
  </div>;
}

function TextInk({ layer }: { layer: Layer }) {
  return layer.runs.length
    ? layer.runs.map((run, index) => <span key={index} style={inkStyle(run)}>{run.text}</span>)
    : <span style={{ ...inkStyle(layer), display: "block" }}>{layer.content}</span>;
}

function InlineText({ value, onChange, onFinish, onCancel }: {
  value: string; onChange: (value: string) => void; onFinish: () => void; onCancel: () => void;
}) {
  const input = useRef<HTMLTextAreaElement>(null);
  useEffect(() => { input.current?.focus(); input.current?.select(); }, []);
  useLayoutEffect(() => {
    if (input.current) {
      input.current.style.height = "auto";
      input.current.style.height = `${input.current.scrollHeight}px`;
    }
  }, [value]);
  return <textarea ref={input} className="board-inline-text" aria-label="直接编辑画布文字" value={value}
    maxLength={2000} spellCheck={false} onChange={event => onChange(event.target.value)} onBlur={onFinish}
    onPointerDown={event => event.stopPropagation()} onDoubleClick={event => event.stopPropagation()}
    onKeyDown={event => {
      event.stopPropagation();
      if (event.nativeEvent.isComposing) return;
      if (event.key === "Escape") { event.preventDefault(); onCancel(); }
      if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) { event.preventDefault(); onFinish(); }
    }} />;
}

export function ScaledArtboard(props: Props) {
  const wrapper = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(400);
  useEffect(() => {
    if (!wrapper.current) return;
    const observer = new ResizeObserver(entries => setWidth(entries[0].contentRect.width));
    observer.observe(wrapper.current);
    return () => observer.disconnect();
  }, []);
  const scale = width / props.project.width;
  return <div ref={wrapper} className="scaled-board" style={{ height: props.screen.height * scale }}>
    <div style={{ transform: `scale(${scale})`, transformOrigin: "top left" }}>
      <Artboard {...props} preview />
    </div>
  </div>;
}
