import { useEffect, useRef, useState } from "react";
import {
  ArrowDown, ArrowRight, ArrowUpRight, Check, CheckCheck, ChevronDown, Circle,
  Download, FileText, ImagePlus, Layers3, Loader2, Maximize2, Menu,
  Plus, RotateCcw, Save, Settings2, Sparkles,
  Upload, WandSparkles, X,
} from "lucide-react";
import { api, downloadName, downloadNamed, fileUrl } from "./api";
import { ScaledArtboard, engineeringTextReady } from "./Artboard";
import type {
  ArtDirection, Asset, ExportRequest, Fact, FoundrySettings, Job, Layers2,
  ModelCall, Project, PromptEdit, Screen, ScreensEdit, TargetModelEdit,
} from "./generated";
import { Settings } from "./Settings";
import { PromptWorkbench } from "./PromptWorkbench";
import type { PromptStep } from "./PromptWorkbench";
import { LongformEditor } from "./LongformEditor";
import { editText } from "./textStyle";
import type { ProjectSummary } from "./generated";

type Tab = "studio" | "facts" | "editor" | "records";
const tabLabels: [Tab, string][] = [["studio", "设计工作台"], ["facts", "事实与来源"], ["editor", "画面编辑"], ["records", "生成记录"]];
const kinds: Record<Screen["kind"], string> = { hero: "首屏", feature: "核心卖点", technical: "技术解析", detail: "产品细节", parameters: "产品参数", lifestyle: "人物场景" };
const roles: Record<Asset["role"], string> = { product: "产品", person: "模特", manual: "说明书", reference: "长图参考" };
const blankSettings: FoundrySettings = { planning_endpoint: "", planning_deployment: "", planning_model: "gpt-6.1-sol", image_endpoint: "", image_deployment: "", image_model: "gpt-image-2.5-sunburst", image_requests_per_minute: 2, image_nodes: [], subscription: "", tenant: "", quality: "medium" };
const messageOf = (e: unknown) => e instanceof Error ? e.message : String(e);

export function App() {
  const [projects, setProjects] = useState<Pick<ProjectSummary, "id" | "name">[]>([]);
  const [project, setProject] = useState<Project | null>(null);
  const [settings, setSettings] = useState(blankSettings);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [calls, setCalls] = useState<ModelCall[]>([]);
  const [tab, setTab] = useState<Tab>("studio");
  const [sid, setSid] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [newOpen, setNewOpen] = useState(false);
  const [modelOpen, setModelOpen] = useState(false);
  const [promptStep, setPromptStep] = useState<PromptStep | null>(null);
  const [promptInstruction, setPromptInstruction] = useState("");
  const [exportOpen, setExportOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [loading, setLoading] = useState(true);
  const [mobileNav, setMobileNav] = useState(false);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [role, setRole] = useState<Asset["role"]>("product");
  const uploadInput = useRef<HTMLInputElement>(null);
  const importInput = useRef<HTMLInputElement>(null);
  const pendingDownloads = useRef<Set<string>>(new Set());
  const startedDownloads = useRef<Set<string>>(new Set());
  const projectRequest = useRef(0);
  const active = jobs.find(j => j.status === "queued" || j.status === "running");
  const locked = busy || !!active;
  const configured = !!(settings.planning_endpoint && settings.planning_deployment && settings.image_endpoint && settings.image_deployment);
  const screen = project?.screens.find(s => s.id === sid) ?? project?.screens[0];

  function chooseProject(p: Project) {
    setProject(p); setSid(p.screens[0]?.id ?? ""); setJobs([]); setCalls([]);
    setTab(p.screens.length ? "editor" : "studio"); setDirty(false); setMobileNav(false); setModelOpen(false); setPromptStep(null); localStorage.setItem("studio-project", p.id);
  }
  async function reload(id: string) {
    const [p, j] = await Promise.all([
      api<Project>(`/projects/${id}`), api<Job[]>(`/projects/${id}/jobs`),
    ]);
    setProject(p); setJobs(j);
    setProjects(list => list.map(old => old.id === p.id ? p : old));
    return p;
  }
  useEffect(() => {
    let ignore = false;
    Promise.all([api<ProjectSummary[]>("/projects?summary=true"), api<FoundrySettings>("/settings")])
      .then(async ([list, config]) => {
        if (ignore) return;
        setProjects(list); setSettings(config);
        const selected = list.find(p => p.id === localStorage.getItem("studio-project")) ?? list[0];
        if (selected) {
          const value = await api<Project>(`/projects/${selected.id}`);
          if (!ignore) chooseProject(value);
        }
      })
      .catch(e => { if (!ignore) setError(messageOf(e)); })
      .finally(() => { if (!ignore) setLoading(false); });
    return () => { ignore = true; };
  }, []);
  useEffect(() => {
    if (!project?.id) return;
    let ignore = false;
    api<Job[]>(`/projects/${project.id}/jobs`)
      .then(j => { if (!ignore) setJobs(j); })
      .catch(e => { if (!ignore) setError(messageOf(e)); });
    return () => { ignore = true; };
  }, [project?.id]);
  useEffect(() => {
    if (!project?.id || tab !== "records") return;
    let ignore = false;
    api<ModelCall[]>(`/projects/${project.id}/calls`)
      .then(c => { if (!ignore) setCalls(c); })
      .catch(e => { if (!ignore) setError(messageOf(e)); });
    return () => { ignore = true; };
  }, [project?.id, tab, active?.updated_at]);
  useEffect(() => {
    if (!active || !project) return;
    let stopped = false;
    const id = project.id;
    const timer = window.setInterval(async () => {
      try {
        const [p, j] = await Promise.all([api<Project>(`/projects/${id}`), api<Job[]>(`/projects/${id}/jobs`)]);
        if (!stopped) { setProject(p); setJobs(j); }
      } catch (e) {
        if (!stopped) { setError(messageOf(e)); window.clearInterval(timer); }
      }
    }, 1600);
    return () => { stopped = true; window.clearInterval(timer); };
  }, [active?.id, project?.id]);
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(""), 5000);
    return () => clearTimeout(timer);
  }, [toast]);
  useEffect(() => {
    if (!project) return;
    for (const job of jobs) {
      if (job.kind !== "export" || job.status !== "succeeded" || !job.result) continue;
      if (!pendingDownloads.current.has(job.id) || startedDownloads.current.has(job.id)) continue;
      startedDownloads.current.add(job.id);
      const ext = job.result.toLowerCase().endsWith(".jpg") ? "jpg" : "png";
      const filename = downloadName(project.name, ext);
      void downloadNamed(fileUrl(project, job.result), filename)
        .then(() => setToast(`长图已下载到浏览器的下载文件夹：${filename}`))
        .catch(e => setError(messageOf(e)));
    }
  }, [jobs, project]);

  async function run(work: () => Promise<void>) {
    setBusy(true); setError("");
    try { await work(); } catch (e) { setError(messageOf(e)); }
    finally { setBusy(false); }
  }
  const canLeave = () => !dirty || window.confirm("当前文字修改尚未保存，确认放弃这些修改？");
  function changeTab(next: Tab) { if (next !== tab && canLeave()) { setTab(next); setDirty(false); } }
  async function startJob(path: string, body?: unknown) {
    if (!project) return;
    if (!configured) { setSettingsOpen(true); return; }
    await run(() => submitJob(path, body));
  }
  async function submitJob(path: string, body?: unknown) {
    if (!project) throw new Error("请先选择项目");
    if (!configured) throw new Error("请先在模型设置中配置 Foundry 模型连接");
    await api<Job>(`/projects/${project.id}${path}`, "POST", body);
    await reload(project.id);
  }
  function openPrompts(step: PromptStep, instruction = "") {
    if (dirty || locked) return;
    setPromptInstruction(instruction); setPromptStep(step);
  }
  async function savePrompt(body: PromptEdit) {
    if (!project) throw new Error("请先选择项目");
    const value = await api<Project>(`/projects/${project.id}/prompts`, "PUT", body);
    setProject(value); setProjects(items => items.map(p => p.id === value.id ? value : p));
    return value;
  }
  async function continuePrompt(step: PromptStep, instruction: string) {
    if (step === "scanned_page_ocr" || step === "extract_facts") return submitJob("/extract");
    if (step === "design_plan") {
      if (project?.screens.length && !window.confirm("这一步只按当前这份设计 prompt 重新规划，不会先整理资料。会替换当前分屏，旧版本保留。确认继续？")) throw new Error("已取消重新规划，提示词仍保留。");
      return submitJob("/plan");
    }
    if (!screen) throw new Error("请先规划分屏");
    if (step === "B_regenerate") {
      if (!screen.layout_from_concept) throw new Error("请先从参考稿提取文字位置。未提取时，这份 prompt 不含避让区，不会被发送。");
      return submitJob("/generate?background_only=true", { screen_ids: [screen.id], route: "B" });
    }
    if (step === "revise_screen_prompts") return submitJob(`/screens/${screen.id}/regenerate`, { instruction, route: "B" });
    if (step === "extract_layout") return submitJob("/extract-layout", { screen_ids: [screen.id], route: "B" });
    if (step === "fit_layout_B") return submitJob("/fit-layout", { screen_ids: [screen.id], route: "B" });
    return submitJob(`/generate${step === "concept" ? "?concepts_only=true" : ""}`, {
      screen_ids: [screen.id], route: "B",
    });
  }
  async function saveScreens(drafts: Screen[]) {
    if (!project) return;
    if (!drafts.length) throw new Error("没有待保存的分屏修改");
    const body: ScreensEdit = { version: project.version, screens: Object.fromEntries(drafts.map(draft => [
      draft.id, { layers: draft.layers, height: draft.height, selected_route: draft.b ? "B" as const : null, approved: false, reviews: draft.reviews },
    ])) };
    const p = await api<Project>(`/projects/${project.id}/screens`, "PATCH", body);
    setProject(p); setProjects(items => items.map(item => item.id === p.id ? p : item));
    setDirty(false); setToast("修改已保存，不会触发生图调用");
  }
  async function addProject(p: Project) {
    setProjects(list => [p, ...list.filter(old => old.id !== p.id)]);
    chooseProject(p); setNewOpen(false); setToast("项目已保存到本地");
  }

  return <div className="app-shell">
    {mobileNav && <button className="sidebar-backdrop" aria-label="关闭导航" onClick={() => setMobileNav(false)} />}
    <aside id="studio-sidebar" className={`sidebar ${mobileNav ? "mobile-open" : ""}`}>
      <button className="icon-btn mobile-close" aria-label="收起项目导航" onClick={() => setMobileNav(false)}><X size={20} /></button>
      <a className="brand" href="/"><span className="brand-mark"><Layers3 size={22} /></span><span>长图工坊<small>LONGFORM STUDIO</small></span></a>
      <div className="project-picker">
        <label className="eyebrow" htmlFor="project-choice">当前项目</label>
        <div className="project-select"><select id="project-choice" disabled={loading || locked} value={project?.id ?? ""} onChange={e => {
          if (!canLeave()) return;
          const id = e.target.value;
          const request = ++projectRequest.current;
          setLoading(true);
          void api<Project>(`/projects/${id}`)
            .then(p => { if (request === projectRequest.current) chooseProject(p); })
            .catch(e => setError(messageOf(e)))
            .finally(() => { if (request === projectRequest.current) setLoading(false); });
        }}><option value="" disabled>选择一个项目</option>{projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select><ChevronDown size={14} /></div>
        <button className="text-button small" disabled={locked} onClick={() => setNewOpen(true)}><Plus size={14} /> 新建项目</button>
      </div>
      <div className="sidebar-section-heading"><span>素材库</span><span className="count">{project?.assets.length ?? 0}</span></div>
      <div className="asset-list">
        {project?.assets.map(asset => <a className="asset-card" key={asset.id} href={fileUrl(project, asset.file)} target="_blank" rel="noreferrer">
          <div className="asset-thumb">{asset.preview ? <img src={fileUrl(project, asset.preview, 160)} alt="" loading="lazy" decoding="async" /> : <FileText size={20} />}</div>
          <div><strong>{asset.name}</strong><span>{roles[asset.role]} · {asset.page_count ? `${asset.page_count} 页` : `${asset.width} × ${asset.height}`}</span></div><ArrowUpRight size={13} />
        </a>)}
        {!project?.assets.length && <p className="small muted asset-empty">产品、人物、说明书。<br />每一份素材，都是设计的起点。</p>}
      </div>
      {project && <div className="upload-control"><select aria-label="上传素材角色" value={role} onChange={e => {
        const selected = (Object.keys(roles) as Asset["role"][]).find(x => x === e.target.value); if (selected) setRole(selected);
      }}>{Object.entries(roles).map(([v, label]) => <option value={v} key={v}>{label}</option>)}</select>
        <button className="upload-button" disabled={locked} onClick={() => uploadInput.current?.click()}><Plus size={16} />添加素材</button>
        <input ref={uploadInput} hidden type="file" accept=".png,.jpg,.jpeg,.webp,.pdf" multiple onChange={e => {
          const files = [...(e.target.files ?? [])]; e.target.value = "";
          run(async () => {
            for (const file of files) {
              const form = new FormData(); form.append("file", file); form.append("role", file.name.toLowerCase().endsWith(".pdf") ? "manual" : role);
              setProject(await api<Project>(`/projects/${project.id}/assets`, "POST", form));
            }
            setToast("素材已导入，可直接规划设计或整理资料");
          });
        }} />
      </div>}
      <div className="sidebar-section-heading screens-heading"><span>长图分屏</span><span className="count">{project?.screens.length ?? 0}</span></div>
      <nav className="screen-nav" aria-label="分屏列表">
        {project?.screens.map((s, i) => <button key={s.id} className={s.id === screen?.id ? "active" : ""} onClick={() => {
          if (tab !== "editor" && !canLeave()) return;
          setSid(s.id); setMobileNav(false); if (tab === "facts" || tab === "records") setTab("editor");
        }}><span className="screen-index">{String(i + 1).padStart(2, "0")}</span><span>{s.title}<small>{kinds[s.kind]} · {s.height}px</small></span>{s.b || s.kind === "parameters" ? <Check size={14} /> : <Circle size={7} />}</button>)}
        {!project?.screens.length && <p className="small muted">阅读资料并规划后，<br />这里会出现全部分屏。</p>}
      </nav>
      <div className="sidebar-bottom"><span className="status-dot" />本地工作空间<span>zh-CN</span></div>
    </aside>
    <div className="work-area">
      <header className="topbar">
        <button className="icon-btn mobile-menu" aria-label="项目与分屏导航" aria-controls="studio-sidebar" aria-expanded={mobileNav} onClick={() => setMobileNav(true)}><Menu size={19} /></button>
        <div className="breadcrumb">工作空间 <span>/</span> <strong>{project?.name ?? "新的创作"}</strong></div>
        <div className="top-actions"><span className="saved"><Check size={12} />{dirty ? "有未保存修改" : "本地持久保存"}</span>
          <button className="secondary compact" disabled={!project || locked || dirty} onClick={() => openPrompts("design_plan")} aria-label="查看或编辑 AI 提示词"><FileText size={16} /><span className="prompt-entry-label">AI 提示词</span></button>
          <button className="icon-btn" onClick={() => setSettingsOpen(true)} aria-label="模型设置"><Settings2 size={18} /></button>
          <button className="primary compact" disabled={!project?.screens.length || locked || dirty} onClick={() => setExportOpen(true)}><Download size={15} />导出作品</button>
        </div>
      </header>
      <div className="workspace-heading"><div><span className="eyebrow">FROM PRODUCT TO STORY</span><h1>{project ? project.name : "让产品，有自己的叙事。"}</h1><p>{project ? project.target_model || "未填写型号 · 不影响生成" : "从真实素材出发，设计可编辑的电商详情长图。"}{project && <> · <button className="text-button small" disabled={locked || dirty} onClick={() => { setError(""); setModelOpen(true); }}>修改型号</button></>}</p></div>
        <div className="connection-pill" onClick={() => setSettingsOpen(true)} role="button" tabIndex={0} onKeyDown={e => { if (e.key === "Enter") setSettingsOpen(true); }}>
          <span className={`status-dot ${configured ? "" : "amber"}`} />{configured ? "Foundry 已配置 · 可验证" : "待连接 Foundry"}<ArrowUpRight size={13} />
        </div>
      </div>
      <div className="tabbar" role="tablist">{tabLabels.map(([key, label]) => <button key={key} role="tab" disabled={loading} aria-selected={tab === key} className={tab === key ? "active" : ""} onClick={() => changeTab(key)}>{label}{key === "records" && calls.length > 0 && <span>{calls.length}</span>}</button>)}
        <span className="tab-caption">图文分离 · 设计不分离</span>
      </div>
      {error && <div className="error-box global-error" role="alert"><span>{error}</span><button className="icon-btn" aria-label="关闭错误" onClick={() => setError("")}><X size={15} /></button></div>}
      {active && <div className="job-bar"><Loader2 size={17} className="spin" /><div><strong>{active.message}</strong><span>模型调用可能计费 · 已完成的结果会持续保存</span></div><progress value={active.progress} max={100} /></div>}
      {!active && jobs[0]?.status === "failed" && <div className="error-box global-error">上次任务失败：{jobs[0].error}<span className="small">已有结果已保留，可查看生成记录后手动重试。</span></div>}
      <main>
        {loading ? <div className="empty-state"><Loader2 className="spin" /><p>打开本地工作空间…</p></div> : !project ? <div className="welcome">
          <span className="eyebrow">A NEW WAY TO ART DIRECT</span><h2>把卖点，<br /><em>变成画面。</em></h2>
          <p>AI 理解产品、组织叙事与视觉。<br />文字保持可编辑，风格来自你的素材，而不是模板。</p>
          <div className="welcome-actions"><button className="primary" onClick={() => setNewOpen(true)} disabled={busy}><Plus size={17} />从空白项目开始</button></div>
          <button className="text-button" onClick={() => importInput.current?.click()}><Upload size={15} />恢复已有项目 ZIP</button>
          <div className="welcome-meta"><span>01 素材与事实</span><ArrowRight size={14} /><span>02 艺术指导</span><ArrowRight size={14} /><span>03 可编辑成品</span></div>
        </div> : <>
          {tab === "studio" && <div className="studio-layout"><section className="studio-main">
            {project.screens.length > 0 && <PlanBoard key={`${project.id}:${project.version}`} project={project} selectedId={screen?.id ?? ""} onSelect={setSid} disabled={locked} onSave={(art, copies) => run(async () => {
              let current = project;
              if (JSON.stringify(art) !== JSON.stringify(project.art_direction)) {
                current = await api<Project>(`/projects/${current.id}/direction`, "PUT", { version: current.version, art_direction: art, invalidate_images: false });
              }
              const changed = current.screens.filter(item => Object.entries(copies[item.id] ?? {}).some(([id, value]) => item.layers.some(layer => layer.id === id && layer.content !== value)));
              if (changed.length) {
                const body: ScreensEdit = { version: current.version, screens: Object.fromEntries(changed.map(item => [item.id, {
                  layers: item.layers.map(layer => {
                    const next = copies[item.id]?.[layer.id];
                    return layer.kind === "text" && next !== undefined && next !== layer.content ? { ...layer, ...editText(layer, next) } : layer;
                  }) as Layers2,
                  height: item.height, selected_route: item.selected_route === "B" ? "B" : null, approved: false, reviews: item.reviews,
                }])) };
                current = await api<Project>(`/projects/${current.id}/screens`, "PATCH", body);
              }
              setProject(current); setProjects(items => items.map(item => item.id === current.id ? current : item));
              setToast("规划修改已保存，没有重新生图");
            })} />}
            {!project.screens.length ? <div className="source-stage">
              <div className="stage-topline"><span className="mini-label">THE STARTING POINT</span><span className="paper-label">原始素材 · 非生成稿</span></div>
              <h2>先理解产品，<br /><span>再定义它的气质。</span></h2>
              <p>不先选模板。让产品、说明书与人物，<br />共同决定这一次的视觉方向。</p>
              {project.assets.find(a => a.role === "product")?.preview ? <img className="source-product" src={fileUrl(project, project.assets.find(a => a.role === "product")!.preview!)} alt="产品原始参考图，尚未生成设计稿" /> : <div className="source-placeholder"><ImagePlus size={48} /><span>添加产品图，开启设计</span></div>}
              <div className="source-stage-footer"><span>素材已就位 <ArrowDown size={13} /></span><span>{project.assets.length} ASSETS / {project.pages.length} PAGES</span></div>
            </div> : <StudioCanvas project={project} screen={screen!} locked={locked || dirty}
              onJob={startJob} onEdit={() => changeTab("editor")}
            />}
          </section><aside className="workflow-panel"><div className="section-title"><h3>创作路径</h3><span className="small muted">WORKFLOW</span></div>
            <WorkflowStep number="01" title="阅读资料并规划" done={!!project.art_direction} text="一个动作背后仍是两步：先整理事实，再规划气质与分屏。事实是结果，不是门禁。提示词在右上角统一查看和修改。"><button className="secondary full" disabled={locked || dirty || (!project.assets.length && !project.pages.length)} onClick={() => {
              const rerun = project.screens.length || project.facts.length;
              if (!rerun || window.confirm("重新阅读并规划会覆盖已编辑的产品资料，并替换当前分屏。旧版本保留。确认继续？")) startJob("/read-and-plan");
            }}><Layers3 size={15} />{project.screens.length ? "重新阅读并规划" : "阅读资料并规划"}</button>{project.facts.length > 0 && <button className="text-button small" onClick={() => changeTab("facts")}>查看整理结果 · {project.facts.length} 条 <ArrowRight size={13} /></button>}</WorkflowStep>
            <WorkflowStep number="02" title="生成全部画面" done={project.screens.length > 0 && project.screens.every(s => s.b || s.kind === "parameters")} text="先生含字参考稿，再从稿面提取位置，然后生成避开这些区域的无字底图。规划坐标不会发给生图模型。"><button className="secondary full" disabled={locked || dirty || !project.screens.some(s => s.kind !== "parameters")} onClick={() => {
              const ids = project.screens.filter(s => s.kind !== "parameters" && (s.stale || !s.concept || !s.layout_from_concept || !s.b)).map(s => s.id);
              if (!ids.length) { setToast("全部分屏已生成；点击左侧分屏可查看和修改"); return; }
              startJob("/generate", { screen_ids: ids, route: "B" });
            }}><WandSparkles size={15} />生成全部画面</button></WorkflowStep>
            <WorkflowStep number="03" title="点击左侧分屏精修" done={project.screens.some(s => s.layout_from_concept || s.kind === "parameters")} text="生成后左侧长图分屏仍可切换。双击文字即可修改，不必到右侧面板改字。"><button className="secondary full" disabled={!screen} onClick={() => changeTab("editor")}><ImagePlus size={15} />打开长图编辑</button></WorkflowStep>
            <div className="workflow-note"><Sparkles size={18} /><p>MVP · 先验证效果<br /><span>不做型号、来源、数字与发布审核；内容与视觉均未经核准。</span></p></div>
          </aside></div>}
          {tab === "facts" && <FactsPanel key={`${project.id}:${project.version}`} project={project} disabled={locked} onSave={facts => run(async () => {
            setProject(await api<Project>(`/projects/${project.id}/facts`, "PUT", { version: project.version, facts }));
            setToast("产品资料已保存，无需审核即可继续设计");
          })} onExtract={() => startJob("/extract")} />}
          {tab === "editor" && (screen ? <LongformEditor key={project.id} project={project} screen={screen} disabled={locked} onSelectScreen={setSid} onDirty={setDirty} onSave={saveScreens} onPrompts={instruction => openPrompts("revise_screen_prompts", instruction)} onRegenerate={instruction => startJob(`/screens/${screen.id}/regenerate`, { instruction, route: "B" })} /> : <EmptyPanel title="先规划，再精修" text="上传素材后可直接规划，模型会为每一屏生成可编辑的设计规格。" />)}
          {tab === "records" && <Records project={project} calls={calls} jobs={jobs} disabled={locked || dirty} onRestore={version => run(async () => {
            if (!window.confirm(`恢复到版本 ${version}？当前版本也会保留。`)) return;
            setProject(await api<Project>(`/projects/${project.id}/revisions/${version}/restore`, "POST", { version: project.version }));
          })} />}
        </>}
      </main>
      <footer className="workspace-footer"><span>让 AI 创造画面，让设计保持可控。</span><span>{project?.screens.length ?? 0} 屏 <i /> {project?.width ?? 1200}px 等宽 <i /> 本地原型</span></footer>
    </div>
    {settingsOpen && <Settings value={settings} onSave={setSettings} onClose={() => setSettingsOpen(false)} />}
    {promptStep && project && <PromptWorkbench key={`${project.id}:${screen?.id ?? ""}`} project={project} screen={screen} initialStep={promptStep} initialInstruction={promptInstruction} disabled={locked || dirty}
      onClose={() => setPromptStep(null)} onSave={savePrompt} onContinue={continuePrompt} />}
    {newOpen && <NewProject onClose={() => setNewOpen(false)} onCreate={p => run(async () => addProject(await api<Project>("/projects", "POST", p)))} />}
    {modelOpen && project && <TargetModelModal key={`${project.id}:${project.version}`} project={project} disabled={locked} error={error} onClose={() => setModelOpen(false)} onSave={target_model => run(async () => {
      const body: TargetModelEdit = { version: project.version, target_model };
      await api<Project>(`/projects/${project.id}/target-model`, "PATCH", body);
      await reload(project.id);
      setModelOpen(false);
      setToast("型号备注已保存，现有素材、事实与设计均不变");
    })} />}
    {exportOpen && project && <ExportModal project={project} disabled={locked} onClose={() => setExportOpen(false)} onExport={request => run(async () => {
      const job = await api<Job>(`/projects/${project.id}/export`, "POST", request);
      pendingDownloads.current.add(job.id);
      setExportOpen(false);
      setToast("正在渲染长图。完成后会自动保存到浏览器的下载文件夹，不再打包 ZIP。");
      await reload(project.id);
    })} />}
    {toast && <div className="toast" role="status"><CheckCheck size={17} />{toast}</div>}
    <input ref={importInput} type="file" accept=".zip" hidden onChange={e => {
      const file = e.target.files?.[0]; e.target.value = ""; if (!file) return;
      const form = new FormData(); form.append("file", file);
      run(async () => addProject(await api<Project>("/projects/import", "POST", form)));
    }} />
  </div>;
}

function WorkflowStep({ number, title, text, done, children }: { number: string; title: string; text: string; done: boolean; children: React.ReactNode }) {
  return <section className={`workflow-step ${done ? "done" : ""}`}><div className="workflow-number">{done ? <Check size={16} /> : number}</div><div><h4>{title}</h4><p>{text}</p>{children}</div></section>;
}

function EmptyPanel({ title, text }: { title: string; text: string }) {
  return <div className="empty-state"><Layers3 size={30} strokeWidth={1} /><h2>{title}</h2><p>{text}</p></div>;
}

function screenStatus(screen: Screen) {
  if (screen.kind === "parameters") return "工程排版";
  if (screen.layout_from_concept && screen.b) return "已叠字";
  if (screen.layout_from_concept) return "已提取排版";
  if (screen.concept) return "已有参考稿";
  return "待生图";
}

function PlanBoard({ project, selectedId, disabled, onSelect, onSave }: {
  project: Project; selectedId: string; disabled: boolean; onSelect: (id: string) => void;
  onSave: (art: ArtDirection, copies: Record<string, Record<string, string>>) => void;
}) {
  const source = project.art_direction;
  const [art, setArt] = useState(source);
  const [copies, setCopies] = useState<Record<string, Record<string, string>>>({});
  if (!source || !art) return null;
  const fields: ["typography" | "lighting" | "materials" | "rhythm" | "photography" | "motifs", string][] = [["typography", "字体"], ["lighting", "光线"], ["materials", "材质"], ["rhythm", "节奏"], ["photography", "摄影"], ["motifs", "图形"]];
  const copyDirty = project.screens.some(item => Object.entries(copies[item.id] ?? {}).some(([id, value]) => item.layers.some(layer => layer.id === id && layer.content !== value)));
  const dirty = copyDirty || JSON.stringify(art) !== JSON.stringify(source);
  return <section className="plan-board" aria-label="规划设计结果"><div className="section-title"><div><span className="eyebrow">DESIGN PLAN / V{project.art_version}</span><input className="plan-title" aria-label="风格名称" value={art.name} disabled={disabled} onChange={event => setArt({ ...art, name: event.target.value })} /></div><span className="small muted">{project.screens.length} 屏 · 文案已定，排版待生图</span></div>
    <textarea className="plan-field plan-rationale" aria-label="设计依据" rows={3} value={art.rationale} disabled={disabled} onChange={event => setArt({ ...art, rationale: event.target.value })} />
    <div className="palette">{art.palette.map((color, i) => <span key={i} style={{ background: color }} title={color} />)}<span className="small muted">本项目专属视觉语言</span></div>
    <dl className="plan-meta">{fields.map(([key, label]) => <div key={key}><dt>{label}</dt><dd><textarea className="plan-field" aria-label={label} rows={2} value={art[key]} disabled={disabled} onChange={event => setArt({ ...art, [key]: event.target.value })} /></dd></div>)}</dl>
    {art.avoid.length > 0 && <p className="small muted">避开：{art.avoid.join(" · ")}</p>}
    <p className="small muted">直接改文案、字体、光线和节奏即可。保存不会重新生图，也不会把现有底图标成过期。坐标不会发给生图模型。</p>
    <ol className="plan-screens">{project.screens.map((item, index) => <li key={item.id}>
      <button type="button" className={item.id === selectedId ? "active" : ""} onClick={() => onSelect(item.id)}><span>{String(index + 1).padStart(2, "0")}</span><strong>{item.title}</strong><small>{kinds[item.kind]} · {item.height}px · {screenStatus(item)}</small></button>
      <p>{item.purpose}</p>
      <p className="small muted">{item.composition}</p>
      <div className="plan-copy">{item.layers.filter(layer => layer.kind === "text" && (layer.content.trim() || copies[item.id]?.[layer.id])).map(layer => <textarea key={layer.id} aria-label={`${item.title}文案`} rows={2} disabled={disabled} value={copies[item.id]?.[layer.id] ?? layer.content} onChange={event => setCopies(current => ({ ...current, [item.id]: { ...current[item.id], [layer.id]: event.target.value } }))} />)}</div>
    </li>)}</ol>
    <button className="secondary plan-save" disabled={disabled || !dirty} onClick={() => onSave(art, copies)}><Save size={15} />保存这些修改</button>
  </section>;
}

function StudioCanvas({ project, screen, locked, onJob, onEdit }: {
  project: Project; screen: Screen; locked: boolean;
  onJob: (path: string, body?: unknown) => Promise<void>; onEdit: () => void;
}) {
  return <section className="canvas-section"><div className="section-title"><div><span className="eyebrow">{kinds[screen.kind]} / {project.width} × {screen.height}</span><h3>{screen.title}</h3></div><button className="icon-btn" onClick={onEdit} aria-label="编辑当前分屏"><Maximize2 size={17} /></button></div>
    <p className="canvas-purpose">{screen.purpose}</p>
    {screen.stale && <div className="warning">设计已变化，可继续使用现有底图或自行重生，不影响导出。</div>}
    <p className="small muted">规划不把坐标发给生图模型。先生成含字参考稿，再按稿面提取位置，最后叠到无字底图上。</p>
    {screen.kind === "parameters" ? <div className="parameter-preview"><ScaledArtboard project={project} screen={screen} /><p className="small muted">参数屏完全工程化，无需调用图像模型。</p></div> : <div className="comparison-grid">
      {(["concept", "background", "final"] as const).map((stage, index) => {
        const variant = stage === "concept" ? screen.concept : screen.b;
        return <article className="comparison-card" key={stage} data-stage-card={stage}>
          <div className="comparison-label"><strong>{["含字参考原图", "无字底图", "工程叠字成图"][index]}</strong><span>0{index + 1}</span></div>
          <ScaledArtboard project={project} screen={screen} route={stage === "concept" ? "concept" : "B"} text={stage === "final" && engineeringTextReady(screen)} emptyLabel={stage === "final" && !engineeringTextReady(screen) ? "参考稿生成并提取位置后，才在这里叠字" : undefined} />
          {variant?.warnings.length ? <p className="small warning">{variant.warnings.join("；")}</p> : null}
          <div className="comparison-bottom">{stage === "final" ? <button className="secondary full" onClick={onEdit}>在完整长图中编辑</button> : variant ? <a className="text-button small" href={fileUrl(project, variant.file)} target="_blank" rel="noreferrer">打开{stage === "concept" ? "本屏含字参考原图" : "本屏无字底图"}<ArrowUpRight size={13} /></a> : <span className="small muted">等待生成</span>}</div>
        </article>;
      })}
    </div>}
    <div className="canvas-actions"><button className={screen.concept ? "secondary" : "primary"} disabled={locked || screen.kind === "parameters"} onClick={() => onJob("/generate?concepts_only=true", { screen_ids: [screen.id], route: "B" })}>生成本屏参考稿</button><button className={screen.concept ? "primary" : "secondary"} disabled={locked || screen.kind === "parameters" || !screen.concept} title={screen.concept ? undefined : "请先生成含字参考稿"} onClick={() => onJob("/generate", { screen_ids: [screen.id], route: "B" })}><WandSparkles size={15} />从参考稿生成本屏底图</button><button className="text-button" onClick={onEdit}>打开完整长图编辑 <ArrowUpRight size={14} /></button></div>
    {screen.kind !== "parameters" && <div><button className="secondary" disabled={locked || !screen.concept} onClick={() => onJob("/extract-layout", { screen_ids: [screen.id], route: "B" })}><Sparkles size={15} />按参考稿对齐文字</button><p className="small muted">对照本屏含字参考稿，重设字号、对齐、位置和分色，不改文案，不重新生图。原排版可从历史恢复。</p></div>}
    {screen.warnings.length > 0 && <details><summary>排版适配记录 · 待人工核对</summary>{screen.warnings.map((warning, index) => <p className="small muted" key={index}>{warning}</p>)}</details>}
  </section>;
}

function FactsPanel({ project, disabled, onSave, onExtract }: { project: Project; disabled: boolean; onSave: (facts: Fact[]) => void; onExtract: () => void }) {
  const [facts, setFacts] = useState(project.facts);
  const [pageId, setPageId] = useState(project.pages[0]?.id ?? "");
  const page = project.pages.find(p => p.id === pageId);
  return <div className="facts-layout"><section><div className="section-title"><div><span className="eyebrow">PRODUCT NOTES</span><h2>从产品资料，到设计表达。</h2></div>{project.target_model && <span className="badge">{project.target_model}</span>}</div>  <p className="muted">这是阅读资料后的整理结果，不是继续设计的门禁。不做型号、逐字引用或数字校验。</p>
    {project.extraction_warnings.map((w, i) => <p className="warning" key={i}>{w}</p>)}
    {!facts.length ? <div className="empty-state"><FileText size={30} /><h3>尚无整理结果</h3><p>在工作台点击「阅读资料并规划」后，事实会显示在这里。也可以只整理、不重新规划。</p><button className="secondary" disabled={disabled || (!project.pages.length && !project.assets.some(a => a.preview && (a.role === "product" || a.role === "person")))} onClick={onExtract}><Sparkles size={15} />只整理资料</button></div> : <>
      <div className="fact-toolbar"><span>{facts.length} 条产品资料 · 无需审核</span></div>
      {facts.map(fact => <article className="fact-card" key={fact.id}><div className="fact-heading"><h3>{fact.label}</h3><span className="badge">{fact.is_claim ? "资料中的宣称" : "产品描述"}</span></div><p className="fact-value">{fact.value}</p>
        <button className="source-link" disabled={!project.pages.some(p => p.id === fact.source_id)} onClick={() => setPageId(fact.source_id)}><FileText size={13} />{project.pages.some(p => p.id === fact.source_id) ? `第 ${project.pages.find(p => p.id === fact.source_id)?.page} 页 · 查看来源` : "暂无来源页码"}</button>{fact.quote && <blockquote>{fact.quote}</blockquote>}
        <label>补充备注（选填）<textarea rows={2} value={fact.condition} disabled={disabled} onChange={e => setFacts(items => items.map(f => f.id === fact.id ? { ...f, condition: e.target.value } : f))} /></label>
      </article>)}
      <button className="primary" disabled={disabled} onClick={() => onSave(facts)}><Save size={16} />保存产品资料</button>
    </>}
  </section><aside className="source-viewer"><div className="section-title"><h3>说明书原页</h3><select value={pageId} onChange={e => setPageId(e.target.value)} aria-label="说明书页码">{project.pages.map(p => <option key={p.id} value={p.id}>第 {p.page} 页</option>)}</select></div>
    {page ? <><a href={fileUrl(project, page.preview)} target="_blank" rel="noreferrer"><img src={fileUrl(project, page.preview)} alt={`说明书第 ${page.page} 页`} /></a><details open><summary>提取原文</summary><pre>{page.text || "扫描页将在整理事实时由视觉模型转录。"}</pre></details></> : <p className="muted">请上传说明书 PDF。</p>}
  </aside></div>;
}

function Records({ project, calls, jobs, disabled, onRestore }: { project: Project; calls: ModelCall[]; jobs: Job[]; disabled: boolean; onRestore: (version: number) => void }) {
  const [versions, setVersions] = useState<{ version: number; updated_at: string }[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    let ignore = false;
    api<{ version: number; updated_at: string }[]>(`/projects/${project.id}/revisions`)
      .then(v => { if (!ignore) setVersions(v); }).catch(e => { if (!ignore) setError(messageOf(e)); });
    return () => { ignore = true; };
  }, [project.id, project.version]);
  return <div className="records-layout"><section><div className="section-title"><div><span className="eyebrow">TRACEABLE CREATION</span><h2>每次创作，都有来路。</h2></div><a className="secondary" href={`/api/projects/${project.id}/report`}><FileText size={15} />下载流程与费用报告</a><a className="secondary" href={`/api/projects/${project.id}/package`}><Download size={15} />保存可编辑项目</a></div>
    <p className="muted">实际模型、prompt、用量和错误均留在本地。费用以 Azure 账单为准，不估算不存在的价格。</p>
    {!calls.length && <EmptyPanel title="还没有模型调用" text="连接并确认模型后，真实调用会出现在这里。" />}
    {calls.map(call => <details className="call-card" key={call.id}><summary><span className={`status-dot ${call.status === "failed" ? "red" : call.status === "running" ? "amber" : ""}`} /><strong>{call.purpose}</strong><span>{call.model}</span><span className="small">{new Date(call.created_at).toLocaleString("zh-CN")}</span></summary><dl><dt>部署</dt><dd>{call.deployment}</dd><dt>端点</dt><dd>{call.endpoint}</dd><dt>用量</dt><dd><pre>{JSON.stringify(call.usage, null, 2)}</pre></dd><dt>参数</dt><dd><pre>{JSON.stringify(call.request, null, 2)}</pre></dd></dl><h4>实际 prompt</h4><pre>{call.prompt}</pre>{call.error && <div className="error-box">{call.error}</div>}</details>)}
    <h3 className="subheading">任务记录</h3>{jobs.map(job => <div className="job-record" key={job.id}><span>{job.kind}</span><span className={`badge ${job.status === "failed" ? "amber-badge" : ""}`}>{{ queued: "排队中", running: "进行中", succeeded: "已完成", failed: "失败" }[job.status]}</span><span>{job.error ?? job.message}</span>{job.result && <a className="text-button" href={fileUrl(project, job.result)} download={job.kind === "export" ? downloadName(project.name, job.result.toLowerCase().endsWith(".jpg") ? "jpg" : "png") : undefined}><Download size={14} />{job.kind === "export" ? "下载长图" : "下载结果"}</a>}</div>)}
  </section><aside className="versions"><span className="eyebrow">VERSION HISTORY</span><h3>设计版本</h3><p className="small muted">当前版本 {project.version}。重生前的设计与图片引用会保留。</p>{error && <p className="error-box">{error}</p>}{versions.map(v => <div className="version-item" key={v.version}><span>v{v.version}<small>{new Date(v.updated_at).toLocaleString("zh-CN")}</small></span><button className="icon-btn" disabled={disabled} onClick={() => onRestore(v.version)} aria-label={`恢复版本 ${v.version}`}><RotateCcw size={15} /></button></div>)}</aside></div>;
}

function NewProject({ onClose, onCreate }: { onClose: () => void; onCreate: (v: { name: string; target_model: string }) => void }) {
  const [name, setName] = useState("");
  const [model, setModel] = useState("");
  return <div className="modal-backdrop" onClick={onClose}><section className="modal small-modal" role="dialog" aria-modal="true" aria-label="新建项目" onClick={e => e.stopPropagation()}><header><h2>一个新的产品故事</h2><button className="icon-btn" onClick={onClose} aria-label="关闭"><X /></button></header><label>项目名称<input autoFocus value={name} onChange={e => setName(e.target.value)} placeholder="例如：全嵌式洗碗机 · 新品详情页" /></label><label>产品型号（选填）<input value={model} maxLength={100} onChange={e => setModel(e.target.value)} placeholder="可以留空，仅作参考备注" /></label><p className="small muted">MVP 不做型号校验，不按此字段筛选资料。创建后上传产品图、模特与 PDF，即可直接规划。</p><button className="primary full" disabled={!name.trim()} onClick={() => onCreate({ name: name.trim(), target_model: model.trim() })}>创建本地项目 <ArrowRight size={16} /></button></section></div>;
}

function TargetModelModal({ project, disabled, error, onClose, onSave }: {
  project: Project; disabled: boolean; error: string; onClose: () => void; onSave: (model: string) => void;
}) {
  const [model, setModel] = useState(project.target_model);
  return <div className="modal-backdrop" onClick={() => { if (!disabled) onClose(); }}><section className="modal small-modal" role="dialog" aria-modal="true" aria-label="修改目标型号" onClick={e => e.stopPropagation()}>
    <header><h2>修改产品型号备注</h2><button className="icon-btn" disabled={disabled} onClick={onClose} aria-label="关闭型号修改"><X /></button></header>
    <label>产品型号（选填）<input autoFocus disabled={disabled} value={model} maxLength={100} onChange={e => setModel(e.target.value)} placeholder="可以留空，仅作参考备注" /></label>
    <p className="small muted">仅作项目备注，不校验，也不按此字段筛选资料。</p>
    <p className="small muted">修改不会清空素材、事实或分屏；历史版本保留。保存本身不调用模型。</p>
    {error && <p className="error-box" role="alert">{error}</p>}
    <button className="primary full" disabled={disabled || model.trim() === project.target_model} onClick={() => onSave(model.trim())}><Save size={16} />保存型号备注</button>
  </section></div>;
}

function ExportModal({ project, disabled, onClose, onExport }: { project: Project; disabled: boolean; onClose: () => void; onExport: (r: ExportRequest) => void }) {
  const [mode, setMode] = useState<ExportRequest["mode"]>("publish");
  const [format, setFormat] = useState<ExportRequest["format"]>("png");
  const filename = downloadName(project.name, format === "jpeg" ? "jpg" : "png");
  return <div className="modal-backdrop" onClick={onClose}><section className="modal small-modal" role="dialog" aria-modal="true" aria-label="导出作品" onClick={e => e.stopPropagation()}><header><div><span className="eyebrow">READY TO DELIVER</span><h2>从画面，到作品。</h2></div><button className="icon-btn" onClick={onClose} aria-label="关闭导出"><X /></button></header><p className="muted">{project.screens.length} 屏 · {project.width} × {project.screens.reduce((n, s) => n + s.height, 0)} px</p>
    <label>导出模式<select value={mode} onChange={e => setMode(e.target.value === "publish" ? "publish" : "draft")}><option value="publish">无水印 · 直接导出</option><option value="draft">草稿 · 含提示水印</option></select></label>
    <label>图片格式<select value={format} onChange={e => setFormat(e.target.value === "jpeg" ? "jpeg" : "png")}><option value="png">PNG · 无损</option><option value="jpeg">JPEG · 适合上传</option></select></label>
    <div className="export-contents"><Check size={14} />下载一张完整长图：{filename}</div><p className="small muted">渲染完成后，浏览器会把它保存到下载文件夹。不再打包 ZIP。可编辑项目仍用生成记录里的「保存可编辑项目」。</p>
    <button className="primary full" disabled={disabled} onClick={() => onExport({ mode, format, slice_height: null })}><Download size={16} />下载完整长图</button>
  </section></div>;
}
