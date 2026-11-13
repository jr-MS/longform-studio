import { useEffect, useRef, useState } from "react";
import { ArrowUpRight, FileText, RotateCcw, Save, SlidersHorizontal, Type, X } from "lucide-react";
import { fileUrl } from "./api";
import { ScaledArtboard, engineeringTextReady } from "./Artboard";
import { editText } from "./textStyle";
import type { Layer, Project, RouteReview, Screen } from "./generated";

type Stage = "final" | "concept" | "background";
const stages: [Stage, string][] = [["concept", "含字参考稿"], ["background", "无字底图"], ["final", "可编辑成图"]];

export function LongformEditor({ project, screen, disabled, onSelectScreen, onDirty, onSave, onRegenerate, onPrompts }: {
  project: Project; screen: Screen; disabled: boolean; onSelectScreen: (id: string) => void;
  onDirty: (value: boolean) => void; onSave: (screens: Screen[]) => Promise<void>;
  onRegenerate: (instruction: string) => Promise<void>; onPrompts: (instruction: string) => void;
}) {
  const [drafts, setDrafts] = useState<Record<string, Screen>>({});
  const [selection, setSelection] = useState<Record<string, string>>({});
  const [stage, setStage] = useState<Stage>("final");
  const [zoom, setZoom] = useState("fit");
  const [viewportHeight, setViewportHeight] = useState(window.innerHeight);
  const [controlsHeight, setControlsHeight] = useState(100);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [instructions, setInstructions] = useState<Record<string, string>>({});
  const frames = useRef(new Map<string, HTMLElement>());
  const controls = useRef<HTMLDivElement>(null);
  const draft = drafts[screen.id] ?? screen;
  const layer = draft.layers.find(item => item.id === selection[screen.id])
    ?? draft.layers.find(item => item.kind === "text") ?? draft.layers[0];
  const changed = project.screens.filter(item => drafts[item.id] && JSON.stringify(drafts[item.id]) !== JSON.stringify(item));
  const dirty = changed.length > 0;
  const editable = stage === "final" && !disabled && !saving;
  const totalHeight = project.screens.reduce((height, item) => height + (drafts[item.id] ?? item).height, 0);
  const instruction = instructions[screen.id] ?? "";
  const index = project.screens.findIndex(item => item.id === screen.id);

  useEffect(() => { onDirty(dirty); }, [dirty, onDirty]);
  useEffect(() => {
    if (!controls.current) return;
    const observer = new ResizeObserver(entries => setControlsHeight(entries[0].target.getBoundingClientRect().height));
    observer.observe(controls.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    const handler = (event: BeforeUnloadEvent) => { if (dirty) { event.preventDefault(); event.returnValue = ""; } };
    const resize = () => setViewportHeight(window.innerHeight);
    window.addEventListener("beforeunload", handler);
    window.addEventListener("resize", resize);
    return () => { window.removeEventListener("beforeunload", handler); window.removeEventListener("resize", resize); };
  }, [dirty]);
  useEffect(() => {
    const frame = frames.current.get(screen.id);
    const bounds = frame?.getBoundingClientRect();
    if (bounds && (bounds.bottom < 100 || bounds.top > window.innerHeight - 80)) {
      frame?.scrollIntoView({ block: "start" });
    }
  }, [screen.id]);
  useEffect(() => {
    const first = project.screens[0];
    if (zoom === "overview" && first) frames.current.get(first.id)?.scrollIntoView({ block: "start" });
  }, [zoom, controlsHeight]);

  function change(target: Screen, update: (value: Screen) => Screen) {
    setDrafts(items => {
      const next = { ...items };
      const value = update(items[target.id] ?? target);
      if (JSON.stringify(value) === JSON.stringify(target)) delete next[target.id];
      else next[target.id] = value;
      return next;
    });
  }
  function textPatch(target: Screen, id: string, content: string): Partial<Layer> {
    const source = (drafts[target.id] ?? target).layers.find(item => item.id === id);
    return source ? editText(source, content) : { content };
  }
  function editLayer(target: Screen, id: string, patch: Partial<Layer>) {
    change(target, value => {
      const update = (item: Layer) => item.id === id ? { ...item, ...patch } : item;
      return { ...value, approved: false, layers: [update(value.layers[0]), ...value.layers.slice(1).map(update)] };
    });
  }
  function selectLayer(target: Screen, id: string) {
    onSelectScreen(target.id);
    setSelection(items => ({ ...items, [target.id]: id }));
  }
  async function save() {
    setError(""); setSaving(true);
    try { await onSave(changed.map(item => drafts[item.id])); setDrafts({}); }
    catch (error) { setError(error instanceof Error ? error.message : String(error)); }
    finally { setSaving(false); }
  }
  const review = draft.reviews.B ?? { composition: 3, cleanliness: 3, consistency: 3, typography: 3, notes: "" };
  function editReview(key: keyof RouteReview, value: string | number) {
    change(screen, item => ({ ...item, reviews: { ...item.reviews, B: { ...review, [key]: value } } }));
  }

  return <div className="editor-layout longform-editor"><section className="editor-canvas">
    <div className="section-title"><div><span className="eyebrow">ONE CONTINUOUS STORY</span><h2>完整详情长图</h2></div><span className="badge">{project.screens.length} 屏 · {project.width} × {totalHeight}</span></div>
    <p className="muted small longform-hint">点击任意屏切换属性，双击成图中的文字直接修改。分屏无缝相连，切屏不会丢失尚未保存的修改。</p>
    <div className="longform-controls" ref={controls}>
      <div className="stage-tabs" role="tablist" aria-label="画面阶段">{stages.map(([value, title]) => <button key={value} role="tab" aria-selected={stage === value} onClick={() => setStage(value)}>{title}</button>)}</div>
      <label className="zoom-label">视图<select aria-label="长图缩放" value={zoom} onChange={event => setZoom(event.target.value)}><option value="fit">适合宽度</option><option value="half">50% 画幅</option><option value="overview">全图总览</option></select></label>
      <button className="secondary compact inspector-toggle" aria-expanded={inspectorOpen} onClick={() => setInspectorOpen(value => !value)}><SlidersHorizontal size={14} />本屏属性</button>
      <button className="secondary compact" disabled={disabled || saving || !dirty} onClick={() => {
        if (window.confirm("放弃所有分屏尚未保存的修改？")) setDrafts({});
      }}>撤销未保存</button>
      <button className="primary compact" disabled={disabled || saving || !dirty} onClick={save}><Save size={14} />{saving ? "保存中…" : "保存修改"}</button>
      <span className="small muted">{dirty ? `${changed.length} 屏待保存` : `已保存 · 版本 ${project.version}`} · 改字不生图</span>
    </div>
    {error && <div className="error-box" role="alert">{error}</div>}
    {stage !== "final" && <p className="stage-explanation">{stage === "concept" ? "每个内容屏各有独立的 AI 含字参考稿，是生成本屏底图的视觉依据。这里不叠加工程文字。" : "这是依据本屏参考稿重生的原始无字底图，不叠加文字或装饰。"} 参数屏不生图，始终直接显示工程排版。</p>}
    <div className="editor-paper longform-paper"><div className="longform-strip" data-longform-strip data-stage={stage} style={{
      width: zoom === "overview" ? Math.max(1, (viewportHeight - controlsHeight - 80) * project.width / totalHeight) : zoom === "half" ? "50%" : "100%",
    }}>
      {project.screens.map((original, position) => {
        const value = drafts[original.id] ?? original;
        const active = screen.id === original.id;
        return <figure key={value.id} ref={element => { if (element) frames.current.set(value.id, element); else frames.current.delete(value.id); }}
          className={`longform-screen ${active ? "active-screen" : ""}`} data-screen-id={value.id} role="group" style={{ scrollMarginTop: controlsHeight + 24 }}
          aria-label={`第 ${position + 1} 屏：${value.title}`} tabIndex={0}
          onClick={() => onSelectScreen(value.id)} onKeyDown={event => {
            if (event.target === event.currentTarget && event.key === "Enter") onSelectScreen(value.id);
          }}>
          <figcaption className="screen-gutter" title={value.title}><span>{String(position + 1).padStart(2, "0")}</span></figcaption>
          <ScaledArtboard project={project} screen={value} route={stage === "concept" && value.kind !== "parameters" ? "concept" : "B"}
            text={value.kind === "parameters" || (stage === "final" && engineeringTextReady(value))}
            emptyLabel={stage === "final" && !engineeringTextReady(value) ? "参考稿生成并提取位置后，才在这里叠字" : undefined}
            selected={active && stage === "final" ? layer.id : undefined}
            onSelect={stage === "final" ? id => selectLayer(original, id) : undefined}
            onMove={editable ? (id, x, y) => editLayer(original, id, { x, y }) : undefined}
            onTextChange={editable ? (id, content, restore) => editLayer(original, id, restore ?? textPatch(original, id, content)) : undefined} />
        </figure>;
      })}
    </div></div>
  </section><aside className={`inspector longform-inspector ${inspectorOpen ? "inspector-open" : ""}`} aria-label="当前分屏属性">
    <button className="icon-btn inspector-close" onClick={() => setInspectorOpen(false)} aria-label="关闭分屏属性"><X size={18} /></button>
    <div className="section-title"><div><span className="eyebrow">SCREEN {String(index + 1).padStart(2, "0")} / {project.screens.length}</span><h3>{screen.title}</h3></div><Type size={18} /></div>
    <p className="small muted">{stage === "final" && engineeringTextReady(screen) ? "双击画布文字原位编辑；Enter 换行，⌘／Ctrl + Enter 完成，Esc 取消本次改字。完成后点击保存修改。" : stage === "final" ? "还没有从参考稿提取文字位置，所以这里不预排工程文字。" : "当前为原始图片预览。切换「可编辑成图」后，可以拖动、双击文字编辑。"}</p>
    <div className="screen-source-links">
      {screen.concept && <a href={fileUrl(project, screen.concept.file)} target="_blank" rel="noreferrer"><FileText size={13} />本屏参考原图<ArrowUpRight size={12} /></a>}
      {screen.b && <a href={fileUrl(project, screen.b.file)} target="_blank" rel="noreferrer">无字底图原图<ArrowUpRight size={12} /></a>}
      {screen.kind === "parameters" && <span className="small muted">参数屏直接排版，无 AI 参考图。</span>}
    </div>
    <fieldset disabled={!editable}>
      <label>当前图层<select value={layer.id} onChange={event => setSelection(items => ({ ...items, [screen.id]: event.target.value }))}>{draft.layers.map((item, position) => <option key={item.id} value={item.id}>{String(position + 1).padStart(2, "0")} · {item.kind === "text" ? item.content.slice(0, 20) || "空文字" : item.kind === "rect" ? "色块／渐变" : "线条"}</option>)}</select></label>
      {layer.kind === "text" && <>
        <label>文字内容<textarea aria-label="文字内容" value={layer.content} maxLength={2000} rows={3} onChange={event => editLayer(screen, layer.id, textPatch(screen, layer.id, event.target.value))} /></label>
        <label>字体<select value={layer.font} onChange={event => {
          const font = (["PingFang SC", "Songti SC", "Heiti SC"] as const).find(value => value === event.target.value);
          if (font) editLayer(screen, layer.id, { font });
        }}><option value="PingFang SC">苹方 · PingFang SC</option><option value="Songti SC">宋体 · Songti SC</option><option value="Heiti SC">黑体 · Heiti SC</option></select></label>
        <div className="field-pair"><label>字号 / px<input type="number" min={12} max={180} value={layer.font_size} onChange={event => editLayer(screen, layer.id, { font_size: event.target.valueAsNumber || 12 })} /></label><label>字重<select value={layer.weight} onChange={event => {
          const weight = ([300, 400, 500, 600, 700, 800] as const).find(value => value === Number(event.target.value));
          if (weight) editLayer(screen, layer.id, { weight });
        }}>{[300, 400, 500, 600, 700, 800].map(value => <option key={value}>{value}</option>)}</select></label></div>
        <div className="field-pair"><label>对齐<select value={layer.align} onChange={event => {
          const align = (["left", "center", "right"] as const).find(value => value === event.target.value);
          if (align) editLayer(screen, layer.id, { align });
        }}><option value="left">左对齐</option><option value="center">居中</option><option value="right">右对齐</option></select></label><label>行距<input type="number" min={1} max={2} step=".05" value={layer.line_height} onChange={event => editLayer(screen, layer.id, { line_height: event.target.valueAsNumber || 1 })} /></label></div>
      </>}
      {layer.kind === "text" && layer.runs.length > 0 ? <div className="run-colors">{layer.runs.map((run, index) => <div key={index}><label>{run.text.split("\n").join("↵")}<input type="color" aria-label={`${run.text}的颜色`} value={run.color} onChange={event => {
        const runs = layer.runs.map((item, position) => position === index ? { ...item, color: event.target.value } : item);
        editLayer(screen, layer.id, { runs, color: runs[0].color });
      }} /></label><GradientControls ink={run} onChange={patch => editLayer(screen, layer.id, {
        runs: layer.runs.map((item, position) => position === index ? { ...item, ...patch } : item),
      })} /></div>)}<p className="small muted">改字保留原分色；新增文字继承相邻颜色，替换文字沿用被替换段的配色。</p></div> : <><label>颜色<input type="color" value={layer.color} onChange={event => editLayer(screen, layer.id, { color: event.target.value, runs: [] })} /></label><GradientControls ink={layer} onChange={patch => editLayer(screen, layer.id, patch)} /></>}
      <label>不透明度<input type="number" min={0} max={1} step=".05" value={layer.opacity} onChange={event => editLayer(screen, layer.id, { opacity: event.target.valueAsNumber || 0 })} /></label>
      <div className="field-pair">{(["x", "y", "width", "height"] as const).map(key => <label key={key}>{{ x: "X / %", y: "Y / %", width: "宽 / %", height: "高 / %" }[key]}<input type="number" min={key === "width" || key === "height" ? 0.1 : 0} max={100} step=".1" value={Math.round(layer[key] * 1000) / 10} onChange={event => editLayer(screen, layer.id, { [key]: (event.target.valueAsNumber || 0) / 100 })} /></label>)}</div>
      <label>画布高度 / px<input type="number" min={480} max={3200} value={draft.height} onChange={event => change(screen, value => ({ ...value, height: event.target.valueAsNumber || 480, approved: false }))} /></label>
      <details className="review-details"><summary>人工视觉评分 · 1—5</summary>{(["composition", "cleanliness", "consistency", "typography"] as const).map(key => <label key={key}>{{ composition: "构图保留", cleanliness: "底图洁净度", consistency: "产品／人物一致性", typography: "图文融合" }[key]}<input type="range" min={1} max={5} value={review[key]} onChange={event => editReview(key, Number(event.target.value))} /><span>{review[key]}</span></label>)}<label>备注<textarea value={review.notes} onChange={event => editReview("notes", event.target.value)} /></label></details>
    </fieldset>
    {screen.stale && <p className="warning small">设计有更新，仍可使用现有底图编辑或导出。</p>}
    <details className="regenerate-details"><summary><RotateCcw size={14} />只重新生成这一屏</summary><p className="small muted">更新本屏参考稿和无字底图，保留人工文字与其他屏。调用可能计费。</p>
      <textarea value={instruction} disabled={disabled} onChange={event => setInstructions(items => ({ ...items, [screen.id]: event.target.value }))} maxLength={2000} placeholder="例如：保持留白，改为明亮的午后卧室" rows={3} />
      <button className="text-button small" disabled={disabled || dirty} onClick={() => onPrompts(instruction)}>在提示词工作台查看本次实际 prompt</button>
      <button className="secondary full" disabled={disabled || dirty || !instruction.trim() || screen.kind === "parameters"} onClick={() => onRegenerate(instruction)}>重新设计本屏底图</button>
    </details>
  </aside></div>;
}

function GradientControls({ ink, onChange }: {
  ink: Pick<Layer, "color" | "gradient_end" | "gradient_angle">;
  onChange: (patch: Pick<Partial<Layer>, "gradient_end" | "gradient_angle">) => void;
}) {
  return <div className="gradient-controls"><label>填色方式<select aria-label="填色方式" value={ink.gradient_end ? "gradient" : "solid"} onChange={event => onChange({
    gradient_end: event.target.value === "gradient" ? ink.gradient_end ?? "#b58d61" : null,
  })}><option value="solid">纯色</option><option value="gradient">渐变色</option></select></label>
    {ink.gradient_end && <div className="field-pair"><label>渐变终点<input aria-label="渐变终点" type="color" value={ink.gradient_end} onChange={event => onChange({ gradient_end: event.target.value })} /></label><label>渐变角度<input aria-label="渐变角度" type="number" min={0} max={360} value={ink.gradient_angle ?? 180} onChange={event => onChange({ gradient_angle: Math.max(0, Math.min(360, event.target.valueAsNumber || 0)) })} /></label></div>}
  </div>;
}
