# 长图工坊 · Longform Studio

[English guide](README.en.md)

在本机运行的电商产品详情长图工作台。上传产品图片、人物图片、产品说明书和可选设计参考，使用自己的 Azure 模型节点生成多屏设计，再编辑文字并下载完整长图。界面使用简体中文。

**本仓库只提供本地工作台，不包含 Container Apps、容器镜像或云基础设施部署。** 不包含客户素材、已生成项目、真实账户配置、账单证据或密钥。第一次启动是空工作空间。

## 1. 工作方式

```text
上传资料
  → 阅读资料并规划：整理事实 → 设计叙事、气质、分屏与文案
  → 每个内容屏：含字参考稿 → 从真实参考稿提取文字排版 → 参考重生无字底图
  → 工程叠字 → 连续长图编辑 → 下载 PNG / JPEG
```

这是原方案 B：**先画参考图，再从图中提取最终文字排版**。规划阶段的坐标仅是草稿，不先把工程文字贴到图片上。确认设计方向后生成全部屏，不先生成三个试验屏。每屏等宽，高度可不同；参数屏直接工程排版，跳过生图。事实页是结果展示，不是审核门禁。

支持查看每屏参考稿、无字底图和工程成图；原位双击改字、拖动、多色文字、双颜色线性渐变；只重生一屏；查看和修改真实调用使用的提示词；项目 ZIP 备份与恢复；离线 HTML 流程、token 与费用报告。

AI 不保证像素级复刻参考稿：重生底图可能改变产品、人物、灯光或图标，字体识别和定位可能有误。发布前须人工核对产品外观、参数、宣称、人物授权和排版。

## 2. 运行条件

| 软件 / 条件 | 用途 |
|---|---|
| Python 3.11+，建议独立虚拟环境 | FastAPI 后端、素材解析、图像合成 |
| Node.js 20.19+ 与 npm | 安装、构建 React/Vite 前端 |
| Git | 获取及更新代码 |
| Azure CLI，终端可运行 `az` | 服务端以自己的 Microsoft Entra 身份认证 |
| Playwright Chromium | 在本机高清渲染和导出 |
| 浏览器与可用中文字体 | 工作台和文字排版 |
| 自己的 Azure 订阅、可用模型部署及配额 | 只有 AI 功能需要；本地编辑不需要 |

依赖安装需要访问 Python/npm/Playwright 下载服务；AI 调用需要访问 Azure。素材和数据保存在本机，但执行 AI 步骤时，相应资料、prompt 和参考图会发送给你配置的 Azure 资源。浏览器不直接取得 Azure 访问令牌。

字体选项目前为 `PingFang SC`、`Songti SC`、`Heiti SC`。macOS 通常已有相应字体；Windows/Linux 缺少时使用系统中文后备字体，**不能保证跨机器版式一致**。Linux 需自行安装中文字体，例如发行版的 Noto CJK 字体包。仓库和项目 ZIP 不分发字体；不要复制未经授权的字体。

## 3. 安装与启动

私有仓库需要所有者先授权客户的 GitHub 账号。授权后可通过 HTTPS 或 SSH 克隆；无需在代码中放 GitHub token。

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

Linux 若缺少 Chromium 系统依赖，按发行版要求执行 `python -m playwright install --with-deps chromium`，该命令可能需要管理员权限。

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

`ExecutionPolicy Bypass` 只应用于本次脚本进程，不修改机器的长期策略。组织禁止执行脚本时，改用下面的直接启动命令，不修改组织策略：

```powershell
$env:STUDIO_ORIGIN = "http://127.0.0.1:8765"
.\.venv\Scripts\python.exe -m uvicorn backend.main:create_app --factory --host 127.0.0.1 --port 8765
```

打开 **<http://127.0.0.1:8765>**；保持终端运行，Ctrl+C 停止。首次不用连接模型即可新建项目、上传资料、恢复项目包、编辑已有成图。

### 自定义端口与目录

macOS/Linux：`PORT=8775 bash run.sh`。Windows：先 `$env:PORT = "8775"`，再运行启动脚本。访问相同端口；脚本同步设置导出使用的 `STUDIO_ORIGIN`。

数据默认在仓库下 `.data/`。可在启动前设置绝对路径 `export STUDIO_DATA_DIR="/path/to/workspace"`；Windows 使用 `$env:STUDIO_DATA_DIR = "D:\LongformData"`。不要同时用多个服务进程打开同一数据目录。应用是单进程、单用户本地原型，不是多租户公网服务。

## 4. 换成自己的 Azure 模型节点

### 4.1 先在 Azure 中准备部署

在 Microsoft Foundry / Azure OpenAI 中创建自己的资源及部署，确认所在区域、账号模型访问权和配额。**应用不会假装不可用模型可用，也不会自动降级。**

| 工作台字段 | 当前支持模型 ID | 真实 API |
|---|---|---|
| 设计 / 规划模型 | `gpt-6.1-sol` | `POST /openai/v1/responses`，需要文本、图像输入及 JSON 输出能力 |
| 图像模型 | `gpt-image-2.5-sunburst` 或 `gpt-image-2.5-flare` | Image API edits / generations，`api-version=2025-04-01-preview` |

**模型 ID 不是部署名。** 例如把规划模型部署命名为 `my-planner`，工作台“部署名称”填 `my-planner`，模型 ID 仍是 `gpt-6.1-sol`。两种模型可以放在不同资源。不是任意兼容 OpenAI 的模型都能直接替换；当前后端会核对部署的真实模型身份。

资源根端点示例：

```text
https://YOUR-PLANNING-RESOURCE.openai.azure.com
https://YOUR-IMAGE-RESOURCE.cognitiveservices.azure.com
```

也接受 Azure 资源的 `*.services.ai.azure.com` 根端点。**不要填 Foundry 项目端点、`/openai/v1/`、`/responses`、查询参数或 API key。** 应复制资源根端点而不是自己拼接未经核实的地址。

### 4.2 登录及权限

在启动后端的同一台机器、同一用户环境中执行：

```bash
az login --tenant YOUR_TENANT_ID
az account set --subscription YOUR_SUBSCRIPTION_ID
az account show --query "{subscription:id,tenant:tenantId}" -o json
```

没有交互浏览器的环境可以使用组织允许的 `az login --use-device-code --tenant YOUR_TENANT_ID`。工作台在服务端通过 `az account get-access-token` 取得短期令牌，不在浏览器或 SQLite 中保存令牌，不支持用 API key 替代登录。

身份需要两类权限：

- 管理面：读取资源与部署信息，例如适当范围的 Reader 权限；资源发现、目录/额度查看还需要相应枚举权限。
- 数据面：模型推理权限，例如资源范围内的 **Cognitive Services OpenAI User**，或组织配置的等效数据操作权限。管理面 Reader 单独不意味着能推理。

如通过界面“明确确认创建图像部署”，另需部署写权限及配额。客户只有 key、没有 CLI 身份或管理面读取权限时，**当前版本不能直接使用**；需管理员配置权限。权限生效可能需要等待并重新登录。

### 4.3 在工作台配置

1. 打开右上角 **模型设置**；填写订阅 ID 和可选租户 ID。留空订阅则使用 Azure CLI 当前订阅。
2. 点击“发现当前订阅资源”，选择已部署的规划/图像模型；也可手动填实际根端点和部署名。
3. 选择图像模型和品质（低 / 中 / 高），设置主图像节点每分钟请求上限。
4. 点击 **保存并验证**。这会保存本机设置并只读核对资源、部署名和模型身份，**不是实际推理测试，不保证剩余额度或内容一定通过**。
5. 上传资料后执行“阅读资料并规划”，可验证本机的真实 Responses 调用；成功及失败都能在“生成记录”查看。随后生成参考稿会真实验证 Image API，并产生费用。

设置保存在当前数据目录的 SQLite，而不是 `.env` 或源代码。仓库中没有内置作者资源，也不会隐式调用未配置的备用节点。

规划调用已迁移到 Responses，没有 Chat Completions 回退。具体映射为 `instructions`、`input_text` / `input_image`、`text.format`、`max_output_tokens=16000`、`store=false`；解析 `output` 中的消息文本和 `input_tokens` / `output_tokens`。JSON schema 使用 `strict=false`，随后由本地 Pydantic 校验，不能称为服务端严格 schema 保证。`store=false` 不替代 Azure 的组织数据处理政策。

### 4.4 多节点生图与 429

在模型设置展开 **可选：添加自己的生图节点，分担限流**，最多添加 4 个额外节点：

| 配置 | 要求 |
|---|---|
| 根端点 | 自己的另一个 Azure 资源；相同端点不能重复 |
| 部署名 | 该资源上实际存在的部署 |
| 模型 | 必须和主图像节点是同一种 Sunburst 或 Flare |
| 订阅 ID | 可不同，留空继承主订阅；必须是当前登录身份可访问的同一租户 |
| 每分钟请求上限 | 按每个节点实际配额填写，默认 2，范围 1—120 |

应用轮询显式配置的节点，分别限速；遇到 429 切换节点，最多 3 轮。配置较高 RPM **不会提高 Azure 授予的额度**；同资源重复部署也不一定增加独立额度。其他 HTTP / 内容策略错误不会伪装成功。切换主模型时同时调整全部备用部署；不会把它们自动改成另一个模型。

同一批备用节点不支持跨租户。要整体切换租户，先停下生成、重新 `az login`，再更换所有相关设置。

### 4.5 要支持其他 GPT 模型

只是换资源或部署名，不需改代码。若要换成当前支持列表之外的模型，需要开发者先确认 Responses、多模态、schema 和 Image API 兼容性，再同步修改 `backend/models.py` 的模型类型/验证、设置界面的模型选项及相关测试，重新生成类型和构建前端。不要只改字符串就声称兼容；不要恢复 Chat Completions 作为静默回退。

## 5. 用户操作指南

1. **新建项目。** 从空白项目开始，填写名称；型号只是可选备注，不筛选资料，也不作为生成门禁。
2. **上传素材。** 分别选择产品、模特、说明书、长图参考角色。图片支持 PNG/JPEG/WebP；说明书为 PDF。只上传有使用权限的资料。
3. **阅读资料并规划。** 一个按钮背后分两步：提取带来源事实，再给出整体叙事、配色、字体方向、光线、各屏文案和设计 prompt；扫描 PDF 转录另计。事实页可回查来源。
4. **调整设计。** 直接在已生成的艺术指导、分屏文案及设计字段中修改；修改 prompt 可使用统一提示词面板。规划结果不是已生成图片。
5. **生成全部分屏。** 确认设计方向后，一次逐屏执行完整流程。生成中的项目禁止并发写入，避免覆盖人工内容；已完成的屏保留。
6. **对照与编辑。** 设计工作台显示每屏参考稿、底图、成图。长图编辑器把全部屏无缝连接，点任一屏打开其属性；原图可单独打开。原参考图里的固化文字不可直接编辑。
7. **修改文字。** 双击工程成图中文字，Enter 换行，Ctrl/⌘+Enter 完成，Esc 恢复本次编辑前的文字和颜色；支持拖动和属性调整。改字保留原有分色，新增文字继承相邻样式；属性中支持双颜色线性渐变及 0—360°方向。预览和导出使用同一填色实现。
8. **保存。** 点“保存修改”；跨屏草稿一次原子保存，冲突不覆盖他人/另一窗口改动。改字、拖动、保存不触发模型，参考稿不会同步改字。
9. **局部重做。** 改构图可只重生当前屏；“按参考稿对齐文字”/排版适配属于额外规划调用，可能计费。存 prompt 不会立即重生已有图。
10. **下载。** 导出作品选择 PNG 或 JPEG，完成后由浏览器下载完整长图，不是 ZIP。下载位置由浏览器设置决定，通常为“下载”目录；若被阻止，允许本地网站下载。项目 ZIP 是独立的备份/恢复功能，不代替成品图片。

工程导出要求所有内容屏已有 B 无字底图；不能将占位或旧 A 图冒充成品。参数屏无需底图。溢出文字不作为审核拦截，但会按画布实际裁切，需自己检查。平台切片规格不应被当成京东/天猫统一要求。

## 6. 提示词、真实调用、token 和价格

### 提示词面板与生成记录的区别

右上角 **AI 提示词** 与步骤旁入口打开同一面板，不是两套 prompt。可编辑 8 个步骤：扫描页转录、资料整理、分屏设计、含字参考稿、参考稿排版提取、无字底图、底图排版适配、单屏重生提示词修订。

面板展示“下一次调用”的指令及动态上下文预览。执行时附带最新资料、图层、设计方向和参考图；尚未生成的上游数据不能提前展示。改动只作用于本项目或选中的本屏，保存/查看/恢复默认均不调用模型。

**生成记录是已经发生的真实调用**：最终 prompt、用途、端点与部署、响应、usage、时间及输入图指纹。HTTP/结构失败也记录。旧记录缺失的原始回复会明确标注，不能用后续项目状态冒充原始模型输出。项目包和 HTML 可能含商业资料及资源地址，分享前检查。

### 调用次数

令 `N` 为非参数内容屏数；不重试、不重生、无扫描转录时：

| 调用 | 次数 | 输出 |
|---|---:|---|
| 整理资料 · Responses | 1 | 事实及来源结构 |
| 规划设计 · Responses | 1 | 艺术指导、多屏文案、prompt 草稿 |
| 含字参考稿 · Image API | N | 各屏参考图 |
| 提取参考稿排版 · Responses | N | 工程文字图层、位置、样式 |
| 参考重生无字底图 · Image API | N | 各屏可叠字底图 |
| 总计 | **2 + 3N** | 不是 2 + 3 × 全部屏数 |

扫描页转录、人工重跑、排版适配、单屏重生额外增加调用。结构校验失败最多再请求一次；HTTP、拒绝、incomplete 或空输出不触发结构重试。图像 429 节点切换的失败尝试也记录。编辑、保存、prompt 预览和 PNG/JPEG 导出调用模型 **0 次**。

### HTML 报告与价格证据

“生成记录”可下载完整离线 HTML，展示用途/次数、真实输入输出、token、调用耗时和任务墙钟时间。缓存及 reasoning token 是子集，不重复加入总 token；旧 Chat Completions usage 只为历史兼容读取，不代表新调用仍用旧 API。

**没有价率或 usage 时显示金额未知，不认定免费。** 仓库不附他人账单，不自动查询客户 Cost Management。需要折算费用时，在当前数据目录放自己核实过的 `model-pricing-evidence.json`：

- 顶层 `rates` → 模型 ID → 资源主机名第一段（例如 `my-resource`）→ 数值价率。
- 规划模型字段：`input`、`cached`、`output`。
- 图像模型字段：`text_input`、`image_input`、`output`。
- 数值单位均为 **USD / 100 万 token**；从自己的合同/当前账单核实，不填写单 token 价格。其他顶层字段可记录核实日期、币种和证据出处。

报告用价率与已返回 usage 折算，不是逐请求账单；其他节点借用同模型价率时会标为估算。共享资源账单不能直接算作某个项目独占费用。模型价率、缓存政策和图像计费随资源/合同变化，不能套用作者历史价格。

## 7. 数据、备份与范围

| 路径 | 内容 |
|---|---|
| `.data/studio.sqlite` | 设置、项目、版本、任务、调用记录 |
| `.data/projects/` | 上传素材、PDF 预览、生成图、导出文件 |
| `backend/` | API、模型连接、流程、渲染与报告 |
| `frontend/src/` | 工作台、连续编辑、prompt/设置界面 |
| `run.sh` / `run.ps1` | 本地启动 |

备份时先停止服务，再复制整个数据目录；只复制 SQLite 会丢图。恢复数据目录后用相同 `STUDIO_DATA_DIR` 启动。单项目 ZIP 包含当前引用的素材、图、图层及调用关联；导入会新建项目，不覆盖原项目，不包括所有历史版本或字体文件。

上传每份最多 25 MB，图片最多 2400 万像素；每项目最多 12 份素材，单 PDF 1—24 页且不加密。ZIP 导入最多 100 MB，解压最多 300 MB；导出最多 6500 万像素。全局最多两个生成任务、每项目最多一个。重启将未完成任务标为中断，**不自动再次付费重跑**。

默认只监听 `127.0.0.1`，没有多用户登录、团队授权或生产运维保障。不要直接绑定公网或用作不受信任用户服务。`.gitignore` 排除数据、凭据文件和部署产物；自己新增文件后仍要检查再提交。代码未附商业授权声明，使用与分发范围应与提供方确认。

## 8. 常见问题

| 现象 | 排查 |
|---|---|
| 页面打不开 | 启动终端是否运行；检查端口；先 `npm --prefix frontend run build`；`curl http://127.0.0.1:8765/api/health` |
| `az` 找不到 | 安装 Azure CLI 并让服务端进程 PATH 可见；重新打开终端后启动 |
| 401 / 403 | 重登正确租户，核对订阅、管理面和数据面权限；Reader 不等于推理权限 |
| 404 / 部署模型不匹配 | 使用根端点和真实部署名；不要填项目端点；部署名不等于模型 ID |
| 429 | 核实额度，降低对应节点 RPM，增加自己的独立资源或申请额度；不要凭空提高 RPM |
| Responses incomplete / 拒绝 / schema 错误 | 在记录中看真实响应；简化过长输入或修正设计要求；不会退回 Chat Completions |
| Chromium 缺失或导出失败 | 在后端的同一虚拟环境安装 Playwright Chromium；Linux 补系统依赖 |
| 换端口后导出失败 | `STUDIO_ORIGIN` 必须指向正在运行的本地后端；不要填前端开发服务器/公网 URL |
| Windows/Linux 字体变了 | 相同字体缺失导致后备字体；安装授权字体或手工调整字宽/字号 |
| 图片或文字不贴合参考稿 | AI 重生不是像素复刻；对照实际参考稿调整图层，必要时仅重做本屏 |
| 重启后项目不见了 | 核对 `STUDIO_DATA_DIR` 和启动目录；不要删除 `.data/` |
| HTML 没价格 | 需要本人的核实价率文件和真实 usage；未知不是 0 |

## 9. 开发与验证

修改前端后重新构建，后端导出使用 `frontend/dist`，不直接使用 Vite 开发页。开发可同时启动后端与 `npm --prefix frontend run dev`；Vite `/api` 默认代理到 8765，改后端端口时同步改 `frontend/vite.config.ts`。

激活虚拟环境后：

```bash
python -m pytest -q
python -m ruff check backend
npm --prefix frontend test
npm --prefix frontend run build
```

Windows 无法激活脚本时，使用 `.\.venv\Scripts\python.exe` 代替 `python`。修改 `backend/models.py` 后运行 `npm --prefix frontend run generate:types`；此命令要求 `python` 指向同一虚拟环境，可临时把 `.venv/Scripts` 加入 PATH。共享 schema/类型已提交，普通安装启动不必重新生成。

测试使用合成资料、隔离临时工作空间、HTTP 模型替身及本地 Chromium，不需要客户原素材，不调用真实付费模型。真实 Azure 能力必须在自己有权限的节点另行验证；通过单元测试不代表节点有额度。

参考文档：[Azure Responses 迁移](https://learn.microsoft.com/azure/developer/ai/how-to/azure-openai-to-responses)、[Azure OpenAI 角色权限](https://learn.microsoft.com/azure/ai-foundry/openai/how-to/role-based-access-control)、[Azure CLI 登录](https://learn.microsoft.com/cli/azure/authenticate-azure-cli-interactively)。
