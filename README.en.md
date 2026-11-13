# Longform Studio · 长图工坊

[中文指南](README.md)

A locally hosted workbench for e-commerce product-detail images. Upload product pictures, optional portraits, a product PDF and optional design references; generate multi-screen designs using **your own Azure model resources**, then edit text and download a continuous image. The application UI is in Simplified Chinese.

**This repository is local-only. It does not include Container Apps, container images or cloud infrastructure deployment.** Customer assets, generated projects, real account settings, billing evidence and credentials are not included. A fresh installation starts empty.

## 1. Workflow

```text
Upload materials
  → Read and plan: extract facts → narrative, art direction, screens and copy
  → Each content screen: reference with text → extract its actual typography → regenerate a text-free background
  → Engineering text overlay → continuous editor → PNG / JPEG download
```

This is the original B workflow: **generate the reference first, then extract the final text layout from the actual image**. Coordinates in the initial plan are drafts, not text overlaid before generation. Confirming the design direction generates all screens, not three trial screens. Screens share a width but may have different heights. Parameter screens are rendered directly without image-model calls. Facts are reviewable results, not approval gates.

Features include reference/background/final comparisons, inline double-click editing, dragging, multicolor text, two-color linear gradients, single-screen regeneration, editable execution prompts, ZIP project backup/restore, and an offline HTML report of the workflow, tokens and costs.

Regeneration is not pixel-perfect replication. Product appearance, people, lighting or icons can drift; font identification and positioning can be wrong. Check product accuracy, specifications, claims, image rights and typography before publication.

## 2. Requirements

| Requirement | Purpose |
|---|---|
| Python 3.11+, preferably in a virtual environment | FastAPI backend, document processing and image composition |
| Node.js 20.19+ and npm | Install/build the React/Vite frontend |
| Git | Retrieve and update the source |
| Azure CLI, with `az` on the backend's PATH | Server-side Microsoft Entra authentication |
| Playwright Chromium | Local full-resolution rendering and export |
| Browser and usable Chinese fonts | Workbench and text rendering |
| Your own Azure subscription, deployments and quota | Required only for AI operations |

Installation needs access to Python/npm/Playwright download services. AI operations need Azure access. Data is stored locally, but executing an AI step sends its relevant documents, prompts and reference images to your configured Azure resource. Browser clients do not receive Azure access tokens.

Current font choices are `PingFang SC`, `Songti SC` and `Heiti SC`, typically available on macOS. Windows/Linux use system fallbacks if these fonts are missing; **cross-machine typography is not guaranteed**. Install suitable Chinese fonts on Linux, such as a distribution's Noto CJK package. Neither the repository nor project ZIP distributes fonts. Do not copy fonts without appropriate rights.

## 3. Install and start

For a private repository, the owner must first grant access to the customer's GitHub account. Clone over HTTPS or SSH after authorization; do not place GitHub tokens in source files.

### macOS / Linux

```bash
git clone https://github.com/jr-MS/longform-studio.git
cd longform-studio
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements.txt
python -m playwright install chromium
npm --prefix frontend ci
npm --prefix frontend run build
bash run.sh
```

On Linux, if Chromium system libraries are missing, follow your distribution's instructions or run `python -m playwright install --with-deps chromium`; this may require administrator permissions.

### Windows PowerShell

```powershell
git clone https://github.com/jr-MS/longform-studio.git
cd longform-studio
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
npm --prefix frontend ci
npm --prefix frontend run build
powershell -NoProfile -ExecutionPolicy Bypass -File .\run.ps1
```

The execution-policy option applies only to this script process, not the machine's permanent policy. If your organization disallows script execution, use the direct command instead; do not override organizational policy:

```powershell
$env:STUDIO_ORIGIN = "http://127.0.0.1:8765"
.\.venv\Scripts\python.exe -m uvicorn backend.main:create_app --factory --host 127.0.0.1 --port 8765
```

Open **<http://127.0.0.1:8765>**. Keep the terminal running; Ctrl+C stops it. Without connecting models, you can create projects, upload materials, restore project packages and edit existing artwork.

### Custom port and workspace

macOS/Linux: `PORT=8775 bash run.sh`. Windows: set `$env:PORT = "8775"` before running the script. Open the matching port. Scripts also set the matching export `STUDIO_ORIGIN`.

Data defaults to `.data/` beneath the repository. Set an absolute path with `export STUDIO_DATA_DIR="/path/to/workspace"` before startup to use another directory; on Windows, `$env:STUDIO_DATA_DIR = "D:\LongformData"`. Do not open one workspace with multiple server processes. This is a single-process, single-user local prototype, not a public multi-tenant service.

## 4. Configure your Azure model resources

### 4.1 Prepare deployments in Azure

Create your own resources and model deployments in Microsoft Foundry / Azure OpenAI. Check regional availability, account access and quota. **The workbench does not simulate unavailable models or silently downgrade.**

| Workbench setting | Currently supported model ID | Actual API |
|---|---|---|
| Planning/design | `gpt-6.1-sol` | `POST /openai/v1/responses`; requires text/image inputs and JSON output |
| Image generation | `gpt-image-2.5-sunburst` or `gpt-image-2.5-flare` | Image API edits/generations, `api-version=2025-04-01-preview` |

**A model ID is not a deployment name.** If you name the planning deployment `my-planner`, enter `my-planner` as its deployment name and keep `gpt-6.1-sol` as its model ID. Planning and image generation can use separate resources. Arbitrary OpenAI-compatible models are not supported automatically; the backend checks the deployed model's identity.

Example resource root endpoints:

```text
https://YOUR-PLANNING-RESOURCE.openai.azure.com
https://YOUR-IMAGE-RESOURCE.cognitiveservices.azure.com
```

Azure `*.services.ai.azure.com` root endpoints are also accepted. **Do not enter a Foundry project endpoint, `/openai/v1/`, `/responses`, query parameters or an API key.** Copy the actual resource root rather than inventing an address.

### 4.2 Sign in and grant permissions

On the backend's machine, under the same OS account/environment:

```bash
az login --tenant YOUR_TENANT_ID
az account set --subscription YOUR_SUBSCRIPTION_ID
az account show --query "{subscription:id,tenant:tenantId}" -o json
```

If permitted by your organization, environments without an interactive browser can use `az login --use-device-code --tenant YOUR_TENANT_ID`. The backend obtains short-lived tokens using `az account get-access-token`. Tokens are not persisted in the browser or SQLite. API-key authentication is not supported.

The identity needs both:

- Management-plane access to read resources and deployments, such as Reader at a suitable scope. Resource discovery and catalog/quota inspection require the corresponding listing permissions.
- Data-plane inference permissions, such as **Cognitive Services OpenAI User** on the resource, or equivalent organizational data actions. Reader alone does not authorize inference.

Explicitly creating an image-model deployment through the UI additionally requires deployment-write permission and quota. **An API key without CLI identity or management read permissions cannot be used directly in this version.** Ask your administrator to configure permissions; propagation and another login may be necessary.

### 4.3 Fill in the workbench settings

1. Open **模型设置** (Model settings). Enter the subscription ID and optional tenant ID. An empty subscription uses the CLI's active subscription.
2. Use **发现当前订阅资源** (Discover resources) to select existing planning/image deployments, or enter their root endpoints and actual deployment names manually.
3. Select the image model and quality (low/medium/high). Set the primary image resource's per-minute limit.
4. Select **保存并验证** (Save and verify). This saves local settings and performs read-only resource/deployment/model checks. **It is not an inference smoke test and does not guarantee available quota or content acceptance.**
5. Upload materials and run **阅读资料并规划** (Read and plan) to exercise real Responses calls. Inspect successful or failed calls in **生成记录** (Generation records). Generating a reference then exercises the Image API and incurs charges.

Configuration is stored in the workspace SQLite database, not in `.env` or source code. There are no built-in author resources or implicit fallback endpoints.

Planning now uses Responses with no Chat Completions fallback: `instructions`, `input_text`/`input_image`, `text.format`, `max_output_tokens=16000`, and `store=false`. Message text is read from `output`; usage uses `input_tokens`/`output_tokens`. JSON schema has `strict=false`, followed by local Pydantic validation; this is not a provider-side strict-schema guarantee. `store=false` does not replace Azure organizational data-processing policies.

### 4.4 Image load balancing and HTTP 429

Expand **可选：添加自己的生图节点，分担限流** (Optional additional image resources) in settings. Up to four additional nodes are supported:

| Field | Requirement |
|---|---|
| Root endpoint | Another resource you own; duplicate endpoints are rejected |
| Deployment name | An actual deployment on that resource |
| Model | The same Sunburst or Flare model as the primary node |
| Subscription | May differ; empty inherits the primary subscription; must be accessible in the signed-in tenant |
| Requests per minute | Match the resource's actual quota; default 2, allowed range 1–120 |

The application round-robins only explicitly configured nodes and rate-limits them separately. HTTP 429 switches nodes, with at most three rounds. A higher configured RPM **does not increase Azure quota**. Multiple deployments on one resource do not necessarily provide independent quota. Other HTTP/content-policy failures are surfaced, not converted into success. If switching the primary image model, update every additional deployment too; the workbench does not change deployed models for you.

A node group cannot span tenants. To change tenants for the whole configuration, stop generation, sign in again and replace all affected settings.

### 4.5 Supporting a different GPT model

Changing resources or deployment names needs no source change. Supporting a model outside the allowlist requires a developer to verify Responses, multimodal inputs, schemas and Image API compatibility, then update `backend/models.py`, UI model choices and relevant tests, regenerate shared types and rebuild. Renaming a string does not establish compatibility. Do not add a silent Chat Completions fallback.

## 5. User guide

1. **Create a project.** Start blank and enter a name. The model-number field is an optional note, not a filter or generation gate.
2. **Upload materials.** Assign product, person, manual or reference roles. Images support PNG/JPEG/WebP; manuals are PDF. Upload only authorized materials.
3. **Read and plan.** One button runs fact extraction, then narrative/art-direction and screen planning. Scanned-page transcription is additional. Inspect factual sources in the facts tab.
4. **Adjust the design.** Edit generated art-direction fields, screen copy and design fields directly. Use the shared prompt panel for execution instructions. A plan is not a generated image.
5. **Generate every screen.** After confirming the direction, execute the complete per-screen workflow. Writes to a generating project are blocked to prevent overwriting edits. Completed screens are preserved.
6. **Compare and edit.** The studio shows reference/background/final versions. The continuous editor joins every screen; clicking a screen selects its properties. Open original images separately. Text baked into a reference is not directly editable.
7. **Edit text.** Double-click engineering text. Enter inserts a line break; Ctrl/⌘+Enter finishes; Esc restores that edit's original text and colors. Drag or use properties. Multicolor styles survive edits; inserted text inherits adjacent styling. Properties support two-color linear gradients and directions from 0 to 360°. Preview and export share the filling implementation.
8. **Save.** Select **保存修改**. Changes across screens save atomically; conflicts do not overwrite another window's edits. Editing/dragging/saving makes no model calls and does not rewrite the reference image.
9. **Redo locally.** Regenerate just one screen to change composition. Reference alignment or background layout adaptation makes additional planning calls and can cost money. Saving a prompt does not immediately regenerate existing images.
10. **Download.** Export as PNG or JPEG. The browser downloads the full continuous image, not a ZIP. The destination is controlled by browser settings, typically Downloads; allow the local site's download if blocked. Project ZIP backup/restore is separate from final-artwork export.

Every content screen needs a B background before export; placeholders or legacy A images cannot masquerade as finished artwork. Parameter screens do not need backgrounds. Text overflow is not an approval gate but is clipped by the canvas, so inspect it yourself. Platform slice dimensions are not universal JD/Tmall requirements.

## 6. Prompts, real calls, tokens and prices

### Preview versus execution records

The top-right **AI 提示词** button and step-specific entries open the same prompt panel, not independent prompt systems. Eight editable steps are available: scanned-page transcription, facts, planning, reference generation, reference-layout extraction, background regeneration, background-layout adaptation, and single-screen prompt revision.

The panel previews the **next** call's instructions and dynamic context. Execution attaches current documents, layers, art direction and references. Upstream results that do not exist yet cannot be previewed. Changes are scoped to this project or selected screen. Viewing/saving/resetting prompts does not call models.

**Generation records describe calls that actually occurred**: final prompt, purpose, endpoint/deployment, response, usage, timing and input-image fingerprints. HTTP/schema failures are recorded. Missing historical raw responses are explicitly labeled, not replaced with later project state. Packages/reports can contain business information and resource addresses; inspect before sharing.

### Number of calls

Let `N` be the number of non-parameter content screens, with no retries, regeneration or scanned-page transcription:

| Call | Count | Output |
|---|---:|---|
| Fact extraction · Responses | 1 | Facts and sources |
| Design planning · Responses | 1 | Art direction, screens, copy and draft prompts |
| Reference generation · Image API | N | Reference images |
| Reference-layout extraction · Responses | N | Editable text layers, coordinates and styles |
| Background regeneration · Image API | N | Text-free backgrounds |
| Total | **2 + 3N** | Not 2 + 3 × all screens |

OCR, user reruns, layout adaptation and single-screen regeneration add calls. Schema validation can trigger one corrective request. HTTP errors, refusals, incomplete responses and empty outputs do not trigger schema retries. Image-node 429 failover attempts are recorded too. Editing, saving, prompt preview and PNG/JPEG export make **zero model calls**.

### HTML reports and pricing evidence

Download an offline HTML report from Generation records to inspect purposes/counts, actual inputs/outputs, tokens, individual call durations and task wall time. Cached/reasoning tokens are subsets, not added again to total tokens. Legacy Chat Completions usage remains readable only for historical compatibility, not for new calls.

**Missing rates or usage means unknown cost, not free.** The repository contains no other customer's bill and does not automatically query Cost Management. To estimate costs, place your verified `model-pricing-evidence.json` in the workspace:

- `rates` → model ID → first resource-hostname component, such as `my-resource` → numeric rates.
- Planning fields: `input`, `cached`, `output`.
- Image fields: `text_input`, `image_input`, `output`.
- All values are **USD per million tokens**, verified against your current contract/bill, not per-token prices. Other top-level fields may record verification date, currency and evidence.

The report multiplies returned usage by rates; this is not per-request billing. Borrowing a rate from another resource with the same model is labeled an estimate. A shared resource's total bill is not one project's exclusive charge. Rates, caching policies and image billing vary; do not reuse the author's historical prices.

## 7. Data, backup and limitations

| Path | Contents |
|---|---|
| `.data/studio.sqlite` | Settings, projects, versions, tasks and call records |
| `.data/projects/` | Uploaded materials, PDF previews, generated images and exports |
| `backend/` | API, model transport, pipeline, rendering and reports |
| `frontend/src/` | Workbench, continuous editor, prompts and settings |
| `run.sh` / `run.ps1` | Local launchers |

Stop the server before backing up the entire workspace. Copying SQLite alone loses images. Restore the directory and start with the same `STUDIO_DATA_DIR`. Project ZIPs contain the current referenced materials, images, layers and call links. Import creates a new project, not an overwrite. ZIPs do not contain every historical revision or font files.

Limits: 25 MB per upload; 24 million pixels per image; 12 assets per project; unencrypted PDFs of 1–24 pages. Project import is limited to 100 MB compressed / 300 MB expanded; export to 65 million pixels. At most two generation tasks globally and one per project. Restart marks unfinished tasks interrupted and **does not automatically repeat paid calls**.

The default listener is `127.0.0.1`. There is no multi-user authentication, team authorization or production operations guarantee. Do not expose it directly to untrusted/public users. `.gitignore` excludes data, credential files and deployment artifacts; still inspect newly added files before committing. No commercial license grant is attached; confirm usage/distribution terms with the provider.

## 8. Troubleshooting

| Symptom | Check |
|---|---|
| Cannot open page | Keep launch terminal running; check the port; build frontend first; `curl http://127.0.0.1:8765/api/health` |
| `az` not found | Install Azure CLI and ensure the backend process can see it on PATH; reopen the terminal |
| 401 / 403 | Correct tenant/subscription, fresh login, management read and inference roles; Reader is not inference access |
| 404 / wrong deployed model | Actual resource root and deployment name; not a project endpoint; deployment name differs from model ID |
| 429 | Check quota, lower node RPM, use your own independent resources or request quota; do not invent more capacity |
| Responses incomplete/refusal/schema error | Inspect the actual response; shorten oversized input or revise requirements; no Chat Completions fallback |
| Missing Chromium/export error | Install Playwright Chromium under the backend's environment; add Linux system dependencies |
| Export fails after port change | `STUDIO_ORIGIN` must point at the running local backend, not Vite or a public URL |
| Different Windows/Linux fonts | Missing fonts cause fallbacks; install licensed fonts or manually adjust typography |
| Reference and final do not align | Regeneration is not pixel-perfect; adjust actual layers or redo just that screen |
| Projects missing after restart | Check `STUDIO_DATA_DIR` and launch directory; do not delete `.data/` |
| No prices in HTML | Provide your verified rate evidence and actual usage; unknown is not zero |

## 9. Development and verification

Rebuild after frontend changes: exports render `frontend/dist`, not the Vite development page. For development run the backend and `npm --prefix frontend run dev` simultaneously. Vite's `/api` proxy defaults to port 8765; update `frontend/vite.config.ts` if changing the backend port.

With the virtual environment activated:

```bash
python -m pytest -q
python -m ruff check backend
npm --prefix frontend test
npm --prefix frontend run build
```

On Windows without script activation, substitute `.\.venv\Scripts\python.exe` for `python`. After modifying `backend/models.py`, run `npm --prefix frontend run generate:types`; its `python` must resolve to that virtual environment, so temporarily add `.venv/Scripts` to PATH if needed. Generated schema/types are committed; normal installation does not require regeneration.

Tests use synthetic materials, isolated temporary workspaces, HTTP model doubles and local Chromium. They do not require private customer assets or make paid Azure calls. Validate actual Azure capabilities on your own authorized resources separately; unit-test success does not establish quota.

References: [Azure Responses migration](https://learn.microsoft.com/azure/developer/ai/how-to/azure-openai-to-responses), [Azure OpenAI role permissions](https://learn.microsoft.com/azure/ai-foundry/openai/how-to/role-based-access-control), [Azure CLI login](https://learn.microsoft.com/cli/azure/authenticate-azure-cli-interactively).
