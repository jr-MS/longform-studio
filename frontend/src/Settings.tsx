import { useState } from "react";
import { Check, ExternalLink, Loader2, Search, X } from "lucide-react";
import { api } from "./api";
import type { DeploymentRequest, FoundrySettings } from "./generated";

type Resource = {
  name: string; group: string; location: string; endpoint: string;
  deployments: { name: string; model: string; version: string; state: string }[];
};
type Discovery = { subscription: string; subscription_name: string; tenant: string; resources: Resource[] };
type Option = { model: DeploymentRequest["model"]; version: string; sku: string; capacity: number; available: number };
type Catalog = { resource: string; group: string; location: string; endpoint: string; subscription: string; options: Option[]; notice: string };

export function Settings({ value, onSave, onClose }: {
  value: FoundrySettings; onSave: (s: FoundrySettings) => void; onClose: () => void;
}) {
  const [draft, setDraft] = useState(value);
  const [discovery, setDiscovery] = useState<Discovery | null>(null);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [resourceName, setResourceName] = useState("");

  async function run(work: () => Promise<void>) {
    setBusy(true); setError(""); setMessage("");
    try { await work(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }

  const set = <K extends keyof FoundrySettings>(key: K, v: FoundrySettings[K]) => setDraft(s => ({ ...s, [key]: v }));
  return <div className="modal-backdrop" onClick={onClose}>
    <section className="modal settings-modal" role="dialog" aria-modal="true" aria-label="Foundry 模型设置" onClick={e => e.stopPropagation()}>
      <header><div><span className="eyebrow">MODEL CONNECTION</span><h2>连接你的创作引擎</h2></div><button className="icon-btn" onClick={onClose} aria-label="关闭设置"><X /></button></header>
      <p className="muted">使用本机 Azure CLI 登录身份，不向浏览器传递密钥。保存配置不会自动生图。</p>
      <label>订阅 ID<input value={draft.subscription} onChange={e => set("subscription", e.target.value)} placeholder="留空使用 Azure CLI 当前订阅" /></label>
      <button className="secondary" disabled={busy} onClick={() => run(async () => {
        const found = await api<Discovery>(`/foundry/discover?subscription=${encodeURIComponent(draft.subscription)}`);
        setDiscovery(found);
        setDraft(s => ({ ...s, subscription: found.subscription, tenant: found.tenant }));
      })}>{busy ? <Loader2 className="spin" size={16} /> : <Search size={16} />}发现当前订阅资源</button>
      {discovery && <div className="resource-list">
        <p className="small muted">{discovery.subscription_name} · 点击部署填入配置，不会自动调用</p>
        {discovery.resources.map(resource => <div className="resource-row" key={resource.name}>
          <div><strong>{resource.name}</strong><span>{resource.location}</span></div>
          <div className="resource-deployments">{resource.deployments.filter(d => d.model === "gpt-6.1-sol" || d.model.startsWith("gpt-image-2.5")).map(d =>
            <button key={d.name} disabled={d.state !== "Succeeded"} onClick={() => {
              if (d.model === "gpt-6.1-sol") setDraft(s => ({ ...s, planning_endpoint: resource.endpoint, planning_deployment: d.name }));
              if (d.model === "gpt-image-2.5-sunburst" || d.model === "gpt-image-2.5-flare") {
                const model = d.model;
                setDraft(s => ({ ...s, image_endpoint: resource.endpoint, image_deployment: d.name, image_model: model }));
              }
            }}>{d.name}</button>)}
            {!resource.deployments.some(d => d.model === "gpt-6.1-sol" || d.model.startsWith("gpt-image-2.5")) && <span className="small muted">未发现所需模型</span>}
          </div>
        </div>)}
      </div>}
      <div className="settings-grid">
        <fieldset><legend>01 / 设计 · gpt-6.1-sol</legend>
          <label>Foundry 根端点<input value={draft.planning_endpoint} onChange={e => set("planning_endpoint", e.target.value)} placeholder="https://资源名.cognitiveservices.azure.com" /></label>
          <label>现有部署名称<input value={draft.planning_deployment} onChange={e => set("planning_deployment", e.target.value)} placeholder="填写实际部署名，不自动假定等于模型名" /></label>
          <p className="small muted">通过 Responses API 负责事实、风格、分屏、排版和所有逐屏 prompt。不自动替换模型。</p>
        </fieldset>
        <fieldset><legend>02 / 图像 · GPT Image 2.5</legend>
          <label>Foundry 根端点<input value={draft.image_endpoint} onChange={e => set("image_endpoint", e.target.value)} placeholder="可与设计模型不同" /></label>
          <label>图像部署名称<input value={draft.image_deployment} onChange={e => set("image_deployment", e.target.value)} /></label>
          <label>图像模型<select value={draft.image_model} onChange={e => set("image_model", e.target.value === "gpt-image-2.5-flare" ? "gpt-image-2.5-flare" : "gpt-image-2.5-sunburst")}>
            <option value="gpt-image-2.5-sunburst">Sunburst · 精细创作</option><option value="gpt-image-2.5-flare">Flare · 日常创作</option>
          </select></label>
          <label>生成品质<select value={draft.quality} onChange={e => set("quality", e.target.value === "high" ? "high" : e.target.value === "low" ? "low" : "medium")}><option value="low">低</option><option value="medium">中</option><option value="high">高</option></select></label>
          <label>主节点每分钟请求上限<input type="number" min={1} max={120} value={draft.image_requests_per_minute} onChange={e => set("image_requests_per_minute", Math.max(1, Math.min(120, e.target.valueAsNumber || 2)))} /></label>
        </fieldset>
      </div>
      <details className="deployment-details"><summary>可选：添加自己的生图节点，分担限流</summary>
        <p className="small muted">不添加时只调用上面的主节点。额外节点必须部署同一个图像模型，使用不同资源端点；可填同一租户下的不同订阅。不会调用任何内置演示账户。</p>
        {draft.image_nodes.map((node, index) => <fieldset key={index}><legend>额外节点 {index + 1}</legend>
          <label>资源根端点<input aria-label={`额外节点 ${index + 1} 端点`} value={node.endpoint} onChange={e => set("image_nodes", draft.image_nodes.map((n, i) => i === index ? { ...n, endpoint: e.target.value } : n))} placeholder="https://备用资源.openai.azure.com" /></label>
          <label>部署名称<input aria-label={`额外节点 ${index + 1} 部署`} value={node.deployment} onChange={e => set("image_nodes", draft.image_nodes.map((n, i) => i === index ? { ...n, deployment: e.target.value } : n))} /></label>
          <label>订阅 ID（留空沿用主订阅）<input aria-label={`额外节点 ${index + 1} 订阅`} value={node.subscription} onChange={e => set("image_nodes", draft.image_nodes.map((n, i) => i === index ? { ...n, subscription: e.target.value } : n))} /></label>
          <label>每分钟请求上限<input aria-label={`额外节点 ${index + 1} 请求上限`} type="number" min={1} max={120} value={node.requests_per_minute} onChange={e => set("image_nodes", draft.image_nodes.map((n, i) => i === index ? { ...n, requests_per_minute: Math.max(1, Math.min(120, e.target.valueAsNumber || 2)) } : n))} /></label>
          <button className="text-button" onClick={() => set("image_nodes", draft.image_nodes.filter((_, i) => i !== index))}>移除节点 {index + 1}</button>
        </fieldset>)}
        <button className="secondary" disabled={busy || draft.image_nodes.length >= 4} onClick={() => set("image_nodes", [...draft.image_nodes, { endpoint: "", deployment: "", subscription: "", requests_per_minute: 2 }])}>添加额外节点</button>
      </details>
      <details className="deployment-details"><summary>缺少图像部署？查看可用的 Flare / Sunburst 配置</summary>
        <p className="small muted">仅在你选定的资源创建一个新部署，不覆盖同名部署，不新建项目或账户。</p>
        <label>目标资源<select value={resourceName} onChange={e => setResourceName(e.target.value)}>
          <option value="">请先发现资源，再选择</option>{discovery?.resources.map(r => <option key={r.name}>{r.name}</option>)}
        </select></label>
        <button className="secondary" disabled={busy || !resourceName || !draft.subscription} onClick={() => run(async () => {
          setCatalog(await api<Catalog>(`/foundry/options?subscription=${encodeURIComponent(draft.subscription)}&resource=${encodeURIComponent(resourceName)}`));
        })}>检查目录与剩余额度</button>
        {catalog && <div className="catalog">
          <p className="small">{catalog.resource} / {catalog.location} · {catalog.notice}</p>
          {!catalog.options.length && <p className="warning">没有通过目录与额度检查的配置；未创建任何部署。</p>}
          {catalog.options.map((o, i) => <div key={i} className="catalog-row"><span>{o.model}<small>{o.version} · {o.sku} · 容量 {o.capacity}</small></span><button disabled={busy} className="secondary" onClick={() => {
            const name = window.prompt("输入新部署名称", o.model);
            if (!name || !window.confirm(`将在 ${catalog.resource}（${catalog.location}）创建 ${name}\n模型：${o.model} / ${o.version}\nSKU：${o.sku}，容量：${o.capacity}\n后续调用按 Azure 账单计费。确认创建？`)) return;
            run(async () => {
              const request: DeploymentRequest = { subscription: catalog.subscription, resource_group: catalog.group, resource: catalog.resource, model: o.model, version: o.version, deployment: name, sku: o.sku, capacity: o.capacity, confirmed: true };
              const result = await api<{ state: string }>("/foundry/deploy", "POST", request);
              setDraft(s => ({ ...s, image_endpoint: catalog.endpoint, image_deployment: name, image_model: o.model }));
              setMessage(`部署状态：${result.state}。请保存并验证就绪状态。`);
            });
          }}>创建</button></div>)}
        </div>}
      </details>
      {error && <div className="error-box" role="alert">{error}</div>}
      {message && <div className="success-box"><Check size={16} />{message}</div>}
      <footer><a className="small muted" href="https://learn.microsoft.com/azure/foundry/openai/how-to/dall-e" target="_blank" rel="noreferrer">模型文档 <ExternalLink size={12} /></a>
        <button className="secondary" disabled={busy} onClick={() => run(async () => { onSave(await api<FoundrySettings>("/settings", "PUT", draft)); setMessage("配置已保存，尚未验证或调用模型。"); })}>仅保存</button>
        <button className="primary" disabled={busy} onClick={() => run(async () => {
          onSave(await api<FoundrySettings>("/settings", "PUT", draft));
          const result = await api<{ message: string }>("/settings/validate", "POST");
          setMessage(result.message);
        })}>保存并验证</button>
      </footer>
    </section>
  </div>;
}
