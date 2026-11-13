import { useEffect, useRef, useState } from "react";
import { ArrowRight, Check, FileText, Loader2, RotateCcw, Save, X } from "lucide-react";
import { api, fileUrl } from "./api";
import type { Project, PromptCatalog, PromptEdit, PromptPreview, Screen } from "./generated";

export type PromptStep = PromptPreview["step"];
const errorMessage = (error: unknown) => error instanceof Error ? error.message : String(error);

type Props = {
  project: Project;
  screen?: Screen;
  initialStep: PromptStep;
  initialInstruction: string;
  disabled: boolean;
  onClose: () => void;
  onSave: (body: PromptEdit) => Promise<Project>;
  onContinue: (step: PromptStep, instruction: string) => Promise<void>;
};

export function PromptWorkbench({ project, screen, initialStep, initialInstruction, disabled, onClose, onSave, onContinue }: Props) {
  const [catalog, setCatalog] = useState<PromptCatalog | null>(null);
  const [step, setStep] = useState(initialStep);
  const [instruction, setInstruction] = useState(initialInstruction);
  const [loadedQuery, setLoadedQuery] = useState("");
  const [error, setError] = useState("");
  const [dirty, setDirty] = useState(false);
  const [working, setWorking] = useState(false);
  const dialog = useRef<HTMLElement>(null);
  const query = new URLSearchParams({ instruction, ...(screen ? { screen_id: screen.id } : {}) }).toString();
  const pending = loadedQuery !== query || catalog?.version !== project.version;
  const unsaved = dirty || instruction !== initialInstruction;

  useEffect(() => {
    let active = true;
    const timer = window.setTimeout(() => {
      api<PromptCatalog>(`/projects/${project.id}/prompts?${query}`)
        .then(value => {
          if (active) { setCatalog(value); setLoadedQuery(query); setError(""); }
        })
        .catch(e => { if (active) setError(errorMessage(e)); });
    }, 150);
    return () => { active = false; window.clearTimeout(timer); };
  }, [project.id, project.version, query]);
  useEffect(() => {
    const handler = (event: BeforeUnloadEvent) => { if (unsaved) { event.preventDefault(); event.returnValue = ""; } };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [unsaved]);
  useEffect(() => {
    function revealStep() {
      const nav = dialog.current?.querySelector<HTMLElement>(".prompt-stage-nav");
      const selected = nav?.querySelector<HTMLElement>('[aria-current="step"]');
      if (nav && selected && nav.scrollWidth > nav.clientWidth) {
        nav.scrollLeft += selected.getBoundingClientRect().left - nav.getBoundingClientRect().left
          - (nav.clientWidth - selected.offsetWidth) / 2;
      }
    }
    revealStep();
    window.addEventListener("resize", revealStep);
    return () => window.removeEventListener("resize", revealStep);
  }, [step, catalog?.entries.length]);
  function close() {
    if (!working && (!unsaved || window.confirm("提示词修改或本次重生要求尚未保存／执行，确认放弃并关闭？"))) onClose();
  }
  function choose(next: PromptStep) {
    if (next === step || working) return;
    if (dirty && !window.confirm("当前提示词尚未保存，确认放弃这些修改？")) return;
    setDirty(false); setStep(next);
  }
  const entry = catalog?.entries.find(item => item.step === step);
  return <div className="modal-backdrop" onClick={close}>
    <section ref={dialog} className="modal prompt-workbench" role="dialog" aria-modal="true" aria-label="AI 提示词工作台" onClick={e => e.stopPropagation()} onKeyDown={event => {
      if (event.key === "Escape") { event.preventDefault(); close(); }
      if (event.key === "Tab") {
        const elements = [...(dialog.current?.querySelectorAll<HTMLElement>("button:not(:disabled), textarea:not(:disabled), input:not(:disabled), select:not(:disabled), a[href], summary") ?? [])].filter(e => e.getClientRects().length);
        const first = elements[0], last = elements[elements.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }
    }}>
      <header><div><span className="eyebrow">BEHIND THE CREATION</span><h2>每一步，都由你掌握。</h2></div><button autoFocus className="icon-btn" disabled={working} onClick={close} aria-label="关闭提示词工作台"><X /></button></header>
      <p className="muted">这里是唯一的提示词入口。上方文本框是可编辑指令；展开「查看实际发送的完整用户 prompt」才是这次调用的全文，与生成时使用同一份。不修改就用当前这份继续。继续只执行当前这一步，可能计费。</p>
      <div className="prompt-workbench-body">
        <nav className="prompt-stage-nav" aria-label="AI 步骤">
          {catalog?.entries.map((item, index) => <button key={item.step} className={item.step === step ? "active" : ""} aria-current={item.step === step ? "step" : undefined} disabled={working} onClick={() => choose(item.step)}>
            <span className="prompt-step-number">{String(index + 1).padStart(2, "0")}</span><span>{item.title}<small>{item.screen_id ? screen?.title : "项目级"} · {item.customized ? "自定义" : "默认"}</small></span>
          </button>)}
          {!screen && <p className="small muted">规划分屏后，这里会出现每屏的参考稿、底图和排版提示词。</p>}
        </nav>
        <div className="prompt-edit-area">
          {error && <div className="error-box" role="alert">{error}</div>}
          {!catalog ? <p role="status"><Loader2 size={16} className="spin" /> 正在读取提示词，不调用模型…</p> : entry ? <PromptEditor key={entry.step} entry={entry} version={catalog.version} project={project} screen={screen}
            disabled={disabled || working} pending={pending} instruction={instruction} onInstruction={setInstruction}
            onDirty={setDirty} onWorking={setWorking} onSave={onSave}
            onContinue={async (selected, value) => { await onContinue(selected, value); onClose(); }}
          /> : <p className="muted">当前项目尚无此分屏步骤，请选择左侧项目级提示词。</p>}
        </div>
      </div>
    </section>
  </div>;
}

function PromptEditor({ entry, version, project, screen, disabled, pending, instruction, onInstruction, onDirty, onWorking, onSave, onContinue }: {
  entry: PromptPreview; version: number; project: Project; screen?: Screen;
  disabled: boolean; pending: boolean; instruction: string; onInstruction: (value: string) => void;
  onDirty: (value: boolean) => void; onWorking: (value: boolean) => void;
  onSave: Props["onSave"]; onContinue: Props["onContinue"];
}) {
  const [draft, setDraft] = useState(entry.instructions);
  const [base, setBase] = useState(entry.instructions);
  const [baseVersion, setBaseVersion] = useState(version);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const dirty = draft !== base;
  useEffect(() => { onDirty(dirty); }, [dirty, onDirty]);
  useEffect(() => {
    if (!dirty) { setBase(entry.instructions); setDraft(entry.instructions); setBaseVersion(version); }
  }, [entry.instructions, version]);
  async function persist(value: string | null) {
    const updated = await onSave({ version: baseVersion, step: entry.step, screen_id: entry.screen_id, instructions: value });
    const text = value ?? entry.default_instructions;
    setBase(text); setDraft(text); setBaseVersion(updated.version); onDirty(false);
    setNotice(value === null ? "已恢复默认提示词，未调用模型。" : "提示词已保存，未调用模型。");
  }
  async function act(action: "save" | "reset" | "continue") {
    setError(""); setNotice(""); onWorking(true);
    try {
      if (action === "reset") await persist(null);
      else if (action === "save" || dirty) await persist(draft.trim() === entry.default_instructions ? null : draft.trim());
      if (action === "continue") await onContinue(entry.step, instruction);
    } catch (e) { setError(errorMessage(e)); }
    finally { onWorking(false); }
  }
  const isProject = entry.screen_id === null;
  const operation = entry.step === "design_plan" ? "继续只发送当前这份设计 prompt，不先整理资料。工作台的「阅读资料并规划」才会先整理再规划。"
    : entry.step === "scanned_page_ocr" || entry.step === "extract_facts" ? "继续只整理资料，不重新规划。扫描页按需转录。"
    : entry.step === "concept" ? "继续只生成本屏含字参考稿，不附带规划坐标。修改提示词后会重新生成。"
    : entry.step === "extract_layout" ? "继续只对照含字参考稿提取文字位置，不重新生图，不改文案。"
    : entry.step === "B_regenerate" ? "继续只按当前这份 prompt 生成无字底图。须先提取文字位置，避让区才会出现在全文里。"
    : entry.step.startsWith("fit_layout_") ? "仅在底图偏离参考稿时微调工程排版，不重新生图；原文字内容由工程层保留。"
    : "按本次要求修订本屏提示词，再生成参考稿与无字底图；自定义生图提示词仍优先。";
  const hasInput = project.pages.length > 0 || (entry.step === "design_plan"
    ? project.assets.length > 0
    : project.assets.some(asset => asset.preview && (asset.role === "product" || asset.role === "person")));
  const canContinue = isProject ? hasInput
    : screen?.kind !== "parameters" && (
      entry.step === "extract_layout" ? !!screen?.concept :
      entry.step === "B_regenerate" ? !!screen?.concept && !!screen.layout_from_concept :
      entry.step === "fit_layout_B" ? !!screen?.b :
      entry.step === "revise_screen_prompts" ? !!instruction.trim() : true
    );
  return <>
    <div className="section-title"><div><span className="eyebrow">{entry.model}</span><h3>{entry.title}</h3></div><span className="badge">{entry.customized ? "自定义指令" : "默认指令"}</span></div>
    <label className="prompt-input-label">给 AI 的提示词<textarea aria-label="给 AI 的提示词" className="prompt-instructions" value={draft} maxLength={32000} disabled={disabled} onChange={event => { setDraft(event.target.value); setNotice(""); }} spellCheck={false} rows={13} /></label>
    <p className="small muted">文本框只是指令，不是完整调用。{dirty ? "尚未保存；继续时会先保存。" : "未修改则直接使用当前指令。"} 下方展开的全文才是实际发送内容，包含自动附带的资料。</p>
    {entry.step === "revise_screen_prompts" && <label>本次重生要求<textarea aria-label="本次重生要求" value={instruction} maxLength={2000} disabled={disabled} onChange={event => onInstruction(event.target.value)} rows={2} placeholder="例如：保持留白，改为明亮的午后卧室" /></label>}
    <div className="prompt-attachments"><FileText size={14} /><span>参考图输入：{entry.image_files.length ? entry.image_files.map((file, i) => <a key={`${file}:${i}`} href={fileUrl(project, file)} target="_blank" rel="noreferrer">{i + 1}. {project.assets.find(a => a.preview === file)?.name ?? (project.pages.find(p => p.preview === file) ? `说明书第 ${project.pages.find(p => p.preview === file)?.page} 页` : "当前生成底图")}</a>) : "当前没有；上游结果将按执行步骤附带。"}</span></div>
    {entry.notes.map((note, i) => <p className="small muted" key={i}>{note}</p>)}
    <details className="prompt-effective"><summary>查看实际发送的完整用户 prompt{pending ? " · 正在更新上下文…" : ""}</summary><pre data-effective-prompt>{draft.trim() + entry.context}</pre></details>
    {entry.system_prompt && <details className="prompt-effective"><summary>查看系统提示词与 JSON 输出格式（只读）</summary><pre>{entry.system_prompt}</pre><p className="small muted">格式修正仅在返回的 JSON 不可用时自动补充，运行后可在生成记录查看实际修正内容；不做业务审核。</p></details>}
    {error && <div className="error-box" role="alert">{error}</div>}
    {notice && <p className="success-box" role="status"><Check size={14} />{notice}</p>}
    <div className="prompt-editor-footer"><p className="small muted">{operation}<br />上下文预览基于当前版本，执行时使用届时的素材和上游结果。</p><div>
      <button className="text-button" disabled={disabled || (!entry.customized && !dirty)} onClick={() => act("reset")}><RotateCcw size={14} />恢复默认</button>
      <button className="secondary" disabled={disabled || !dirty || !draft.trim()} onClick={() => act("save")}><Save size={14} />保存提示词</button>
      <button className="primary" disabled={disabled || pending || !canContinue || !draft.trim()} onClick={() => act("continue")}>{dirty ? "保存并继续下一步" : "继续下一步"}<ArrowRight size={14} /></button>
    </div></div>
  </>;
}
