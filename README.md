# 申论进阶 · Shenlun Studio

可部署在 Mac mini Docker Desktop 上的个人申论训练应用。试卷、原答案与批改历史保存在本机；仅主动调用模型时，将相关材料发送给你选择的 API。界面无需访问外部 CDN。

## 不使用 git clone 的安装方式（推荐）

在 Mac mini 安装并启动 Docker Desktop 后，终端运行：

```bash
curl -fsSL https://raw.githubusercontent.com/krbedison-cloud/writing/main/install.sh | bash
```

安装器优先拉取 `ghcr.io/krbedison-cloud/writing:v0.1.2` 多架构镜像，Docker 自动选择 M 系列的 ARM64 或 Intel 的 AMD64。若 GHCR 尚未开放匿名拉取，会从公开的 GitHub Release 下载对应架构的预构建镜像并执行 `docker load`；发行文件仍在生成时才下载同版本源码归档并在本机 `docker build`。整个过程不需要 Git、Python 或 Node。

配置保存在 `~/shenlun-studio/.env`，程序只监听本机 `18086` 端口；数据使用 Docker 数据卷 `shenlun-studio-data`。安装脚本会输出实际访问地址和停止命令。重复运行安装命令不会覆盖已有 `.env`；如版本配置文件已有人工修改，会停止而不是覆盖。发布的新版本可以通过再次执行安装命令更新，既有数据保留。

不希望通过管道执行脚本时，可先下载并查看再执行：

```bash
curl -fL https://raw.githubusercontent.com/krbedison-cloud/writing/main/install.sh -o install-shenlun.sh
bash install-shenlun.sh
```

已有安装或需要指定端口时，执行下面命令，仅更新端口并保留其他配置、密钥和数据：

```bash
curl -fsSL https://raw.githubusercontent.com/krbedison-cloud/writing/main/install.sh | SHENLUN_PORT=18086 bash
```

也可把 `18086` 换为其他 1–65535 的空闲端口。未指定 `SHENLUN_PORT` 时，已有 `.env` 的端口保持原值；全新安装默认使用 `18086`。

使用自定义安装目录：

```bash
curl -fL https://raw.githubusercontent.com/krbedison-cloud/writing/main/install.sh -o install-shenlun.sh
SHENLUN_DIR="$HOME/Applications/shenlun-studio" bash install-shenlun.sh
```

公开镜像可直接拉取：

```bash
docker pull ghcr.io/krbedison-cloud/writing:v0.1.2
```

GitHub Actions 在推送 main 时运行测试，根据 `VERSION` 构建两种架构镜像并创建对应标签的 Release。发布新版本时同步更新 VERSION、安装脚本默认版本与部署配置。GHCR 首次创建的包可能是私有的；仓库拥有者可到 GitHub 的 Packages → writing → Package settings 将包设为 Public，之后无需登录即可拉取。即使没有更改包的可见性，上面的安装器仍能使用公开 Release 中的预构建 Docker 镜像完成安装，不必登录 GHCR。

## 在 Mac mini 上启动

安装并启动 [Docker Desktop for Mac](https://www.docker.com/products/docker-desktop/)，将本仓库放到 Mac，终端进入仓库目录：

```bash
cp .env.example .env
docker compose up -d --build
```

在 Mac 浏览器打开 `http://localhost:18086`。默认仅监听本机，Apple Silicon（M 系列，arm64）和 Intel（amd64）使用同一份配置，无需指定 `platform`。应用本身建议至少分配 2 GB 内存；本地运行大模型需要另计内存。

```bash
docker compose ps
docker compose logs --tail=80
docker compose down                  # 停止，保留数据
```

首次构建需要下载 Python 依赖和中文 OCR。若端口占用，在 `.env` 修改 `APP_PORT` 后重新启动。更新代码后再次运行 `docker compose up -d --build`。不需要 Node、数据库服务或 GPU。

## 使用流程

1. **模型设置**：添加任意数量接口，每个配置对应一个具体模型。输入名称、协议、API 根地址、模型 ID、密钥并测试连接。相同服务商的不同模型可以分别添加。
2. **真题练习 → 导入 PDF**：提取文字；扫描页使用内置 Tesseract 中文 OCR。规则自动识别年份、省份、卷别与题目。也可选择模型进行 AI 识别；这会将完整试卷文本发送给该模型。
3. **校对试卷**：核对原题、给定材料、来源、字数和训练档。可调整题型、增减题目。勾选确认后开始作答。自动识别结果始终需要确认，不会把不确定的题目当作正确答案。
4. **作答**：浏览材料，输入答案；当前浏览器自动保留草稿。勾选多位批改模型（单次最多 12 位），选择一位主模型，提交后原答案立即存档。
5. **批改报告**：各模型独立打分，提供六维评价、具体扣分证据、改进建议与自己的参考范文；主模型基于这些结果和你的原答案进行汇总。程序计算有效模型的算术均分和最高最低分差。
6. **练习历史**：回看每次原答案、当时题干与材料快照、模型身份、评价与范文。修改试卷或模型名称不会改写既有结果。支持导出全部试卷和历史 JSON（不含 API Key）。
7. **能力与竞争力**：每次有效批改后，主模型自动分析全部历史摘要，更新长期诊断；数值统计即时更新。也可手动更新文字诊断。

### 训练档与预估方式

按用户确认的训练结构归类，保留原题文体要求：

| 训练档 | 满分 | 示例 |
|---|---:|---|
| 概括 / 短简报 | 20 | 短概括、350 字简报 |
| 应用文 / 短评 | 30 | 公文、500–600 字短评 |
| 大作文 | 50 | 1000–1200 字议论性文章 |

已用提供的《2026年浙江省公考〈申论〉题（A卷）》验证：17 页可直接提取文字；第一题短简报 350 字、第二题短评 600 字、第三题大作文 1000–1200 字分别归入 20 / 30 / 50 分档。原题即使分数不同，也按训练档评分。

- 六维：审题立意、要点提炼、材料运用、结构逻辑、语言表达、文体规范。先对每次作答的有效模型维度分求平均，再对全部有效作答等权平均，每维满分 100。
- 浙江三题预估：每类题取最近 5 次有效作答的平均分，再按 20 + 30 + 50 相加。缺任一类有效练习时，显示缺失题型，不用零分填充，也不输出总分。
- 失败模型不计零分；只要存在有效批改，该作答即可参与统计，即使主模型汇总失败。报告标明成功数量、缺失结果和模型分差。
- XP 每份成功批改的新答案 +25；同题同答案不重复奖励。每 100 XP 升一级。等级代表坚持，分数代表本次表现，均不代表真实竞争排名。
- 这是一套训练估计，不是经过真实考场样本校准的预测模型。题目难度、反复作答和模型偏差会影响结果；建议固定模型组观察趋势。没有真实考生样本时，不生成百分位、排名或上岸概率。

## 接口配置

| 协议 | API 根地址示例 | 说明 |
|---|---|---|
| OpenAI 兼容 | `https://api.openai.com/v1` | 追加 `/chat/completions` |
| OpenAI 兼容 | `https://api.deepseek.com/v1` | 其他兼容平台按其文档填写 |
| Anthropic | `https://api.anthropic.com/v1` | 追加 `/messages` |
| Gemini | `https://generativelanguage.googleapis.com/v1beta` | 追加 `/models/{模型ID}:generateContent` |
| OpenAI 兼容 / Ollama | `http://host.docker.internal:11434/v1` | Mac 宿主机的本地模型；密钥可留空 |

模型 ID 请向服务商确认，不能填产品显示名称。选择能输出 JSON、上下文足够容纳整份试卷的文本模型。默认输出上限 6000 tokens，可调整到 32000；推理模型可能需要更高上限。程序对返回结构、六维范围、题目满分与输出截断进行校验。部分 OpenAI 兼容服务不支持 `max_completion_tokens` 时，自动改用其支持的 `max_tokens` 重试。

每份答案大约调用“所选模型数 + 主模型汇总 + 主模型画像”次；可选的 AI 试卷识别另外调用。画像的输入包含全部有效历史的压缩证据；记录很多时采用分块汇总。服务商按实际 token 计费，应用不包含 API 额度。单次请求超时为 240 秒，全局最多 4 个并发 API 请求。批改与画像是后台任务，关闭浏览器不影响执行。

某个模型失败后，**重试失败项**只重跑失败的模型和汇总；已有成功模型不会再次收费。全部模型成功但汇总失败时，也只重新汇总。服务重启会把正在执行的任务标记为中断，保留作答与已完成结果，允许手动重试。重试时接口协议、地址和模型 ID 必须与提交快照一致；避免把新模型混进旧评分。密钥可以更新。

## 保存、备份与访问控制

数据保存在 Compose 命名卷 `shenlun-data`，容器路径 `/data`，包括：

- `writing.db`：SQLite 试卷、答案、模型配置、批改结果、任务与画像；
- `master.key`：加密 API Key 的本地密钥，首次启动生成；
- `uploads/`：原始 PDF。

重建容器和 `docker compose down` 保留数据。**不要运行 `docker compose down -v`**，该命令会删除数据卷。应定期备份整个目录，数据库和 `master.key` 需成对保存。不要把数据卷、备份、`.env` 或真实密钥提交到 Git。密钥加密属于本地应用保护，不能抵御已能读取整个数据卷的人。

在仓库目录备份（先停止服务保证 SQLite 文件一致，避免遗漏 WAL）：

```bash
mkdir -p backups
docker compose stop
docker compose cp shenlun:/data "./backups/data-$(date +%Y%m%d-%H%M%S)"
docker compose start
```

完整恢复时，先停止服务，将备份内容放回 `/data` 并确保属主为容器用户 UID/GID `10001` 后再启动。应用内 JSON 导出适合阅读与归档，不包含密钥和原 PDF，**不是完整恢复包**。

`.env` 可设置 `APP_PASSWORD`，浏览器会要求用户名 `admin` 和此密码。默认仅本机访问；如需局域网访问，应先设置密码，修改 Compose 端口绑定并将访问域名或 IP 加入 `ALLOWED_HOSTS`。不要直接暴露到公网；通过带 HTTPS 的反向代理访问时，需要配置正确的 Host 和转发协议。当前定位是单人私有应用，未实现多人账号与权限隔离。

## 本地开发与测试

Python 3.12：

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

默认本地数据目录为仓库 `data/`，可通过 `DATA_DIR` 改为其他目录。采用单进程后台队列与 SQLite，**保持一个 worker、一个应用容器**。非 Docker 开发若需要扫描件 OCR，Mac 可执行 `brew install tesseract tesseract-lang`，确保 `tesseract --list-langs` 包含 `chi_sim`。

可选浏览器完整流程验证（调用本地模拟模型，不消耗真实额度，数据在临时目录，结束清理）：

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/playwright install chromium
.venv/bin/python tests/browser_smoke.py
# 或使用自己的真题文件
.venv/bin/python tests/browser_smoke.py --pdf /path/to/exam.pdf
```

`CHROMIUM_EXECUTABLE` 可指定系统 Chromium。自动测试涵盖题型和来源识别、接口协议、范围校验、均分、部分失败重试、原答案快照、密钥加密、缺题型预估、画像历史、重启恢复、访问控制；浏览器测试覆盖上传→校对→草稿→三题批改→历史→画像以及移动布局。

## 实现与边界

FastAPI + SQLite + 原生 JavaScript/CSS；PyMuPDF 提取，Tesseract 中文 OCR；无外部前端依赖。题目归类提供规则默认值、可选 AI 辅助与人工校对。PDF 中的文字作为待分析数据，模型提示中明确禁止将其当作系统指令。

单文件最大 25 MB / 80 页，提取文字最大 16 万字符；单次模型上下文保护上限 16 万字符（仍需模型本身支持相应上下文）。复杂排版、手写、模糊扫描件可能识别不准，请校对或先 OCR；应用接收键入/粘贴的答案，目前不做手写答案识别。范文为各模型生成的参考文本，未接入官方评分库。
