# Phase 6 · 任务简报 — 高质量信息工作流
## Switchboard 的第二个真实工作流(多源采集 + 去噪 + 多视角解读)

> 分支 `phase6`(从已合并 Phase 5 的最新 master 切)。同一 session 内做完,带 review 闸。

---

### 1. Context 与目标

Phase 5 落地了 LangGraph 引擎 + 多代理编排 + 人在环。Phase 6 建**第二个真实工作流**,也是你真正想要的产出。

**为什么是它**:digest 太简单、证明不了多代理价值;这个任务(多源 + 去噪 + 多视角)**复杂到能让多代理的价值现形**,而且给 Phase 7"工作流数据化"提供**第二个具体工作流**来归纳(从一个例子抽象是过早抽象)。

**目标**:每天定时(如 6:00)产出一份**跨领域高质量信息简报**——
多个 RSS 源(科技/金融/政治/商业)采集 → **过滤掉噪音/炒热度,只留真有价值的** → 每条:**摘要 + 多视角见解(商业/政策/技术…)+ 原始连接** → 组装 → 渲染/入库/邮件(复用现有管线 + 排程)。

---

### 2. 范围 / 非目标

- **范围**:多源 RSS 采集层、价值/噪音过滤、逐条摘要 + 多视角解读、组装、投递、可排程。作为**手写工作流**跑在现成 LangGraph 引擎上。
- **非目标(后续 phase)**:工作流数据化 / 通用编排器(Phase 7)、网页合成器(Phase 8)、X/Twitter 源(现在抓不到)、工作区。**feed 清单做成 config(数据),但不做完整的数据化系统**——那是 Phase 7。

---

### 3. 设计约束 / 红线(继承全系统)

- **新代理只经 `llm.py` 调模型**(模型无关接缝、`tools=[]`);LangGraph 负责编排,不直连 SDK。
- **新产出契约**(见 §5),`title/link` **从来源定位、非模型回填**(同 Digest 的防伪造规则)。
- **复用现有管线**:Brief 渲染成 Markdown + 邮件,走 render/store/email + 排程(不重造)。
- **离线确定性测试**:mock `llm` 接缝 + mock feed 抓取(fixture RSS);**不设 `ANTHROPIC_API_KEY`**;真抓取/真发信在你本地 E2E。
- **成本上限**:每源取 N 条、过滤后留 ≤M 条、每条视角数固定(像 digest 的成本闸)。给具体数:每源 ≤20、过滤后 ≤8、视角固定 3 个 → 单次约 ≤(8×(1 摘要 + 3 视角) + 1 过滤)≈ 33 次调用。
- **web 层守卫继续绿**:新代码都在 worker 侧(web 不 import 它)。

---

### 4. 来源清单(起始 config,全走 RSS)

```
科技:
  https://hnrss.org/frontpage?points=100
  https://mshibanami.github.io/GitHubTrendingRSS/daily/all.xml
  https://github.blog/feed/
金融:
  https://www.cnbc.com/id/10000664/device/rss/rss.html
  https://www.cnbc.com/id/10001147/device/rss/rss.html
政治:
  https://feeds.bbci.co.uk/news/politics/rss.xml
  https://www.politico.com/rss/politicopicks.xml
商业:
  https://feeds.bbci.co.uk/news/business/rss.xml
```

- 全部用一个 RSS 解析器(如 `feedparser`)统一抓取;**source = {domain, url}** 列表存成 config(YAML/py 常量皆可),为 Phase 7"来源即数据"留缝。
- X 暂缺(抓不到);后续以大佬的 Substack/博客 RSS 补入,同一解析器即可。

---

### 5. 产出契约(新)

```
Perspective: { stance: str, take: str }          # stance 如 "商业"/"政策"/"技术"
BriefItem:   { title, link, source, domain, summary: str, perspectives: list[Perspective] }
Brief:       { date, items: list[BriefItem] }
```

- `title`/`link`/`source`/`domain` **来自采集层的原始项**(不可由模型改写);`summary`/`perspectives` 由代理产生。
- stance 列表是 config(默认 3 个:商业/政策/技术),你可改。

---

### 6. 多 agent 形状(为什么这是多代理活)

```
采集(per source,可并行) → 规整为统一 item + 去重
        │
        ▼
过滤 agent:逐条判 "真价值 vs 噪音/炒热度",丢掉噪音(专注的批判判断)
        │  保留 ≤M 条
        ▼
每条 kept item:
   摘要 agent(1 次) + 多视角 agent(N 个不同立场 prompt = 多 agent 扇出/抗锚定)
        │
        ▼
组装 Brief → 渲染/入库/邮件(复用管线)
```

**价值所在**:过滤 agent(一个聚焦的"是否真有价值"判断)+ 多视角 agent(不同立场各一个 prompt,新鲜上下文、互不锚定)——这正是单个超载 agent 做不好、而 digest 体现不出的多代理价值。

---

### 7. 单元拆解 + 验收门

> 估 **2 unit**(plan 时若 Unit 2 过重,可拆 3:过滤+摘要 / 多视角+组装)。

**Unit 1 — 多源采集层**
- 一个 RSS 抓取器(`feedparser`)+ feed config → 规整成统一内部 item(title/link/source/domain/published/raw text)→ 去重。
- 抓取层独立、可注入(测试用 fixture RSS,离线;真抓取你本地验)。
- **门**:离线测试绿(fixture feeds → 规整 + 去重正确);你本地真抓一次确认各源能解析。

**Unit 2 — 编排 + 过滤 + 多视角 + 产出**
- LangGraph 图:gathered items → 过滤节点(过滤 agent) → 每条 kept:摘要 + N 视角(经 `llm.py`)→ 组装 `Brief`。
- 新 `Brief` 契约 + 渲染(Markdown)→ 复用 render/store/email。
- 新工作流入口 `build_brief(items, …) -> Brief`(对称 `build_digest`)。
- 成本上限按 §3。
- **门**:离线测试绿(过滤/摘要/视角/组装/上限/空输入);**真 SDK + 真抓取 + 真发信 E2E 一次**(`run-once` 选 brief 工作流 → 抓取 → 过滤 → 多视角 → 邮件到信箱)。

---

### 8. 工作流选择器(第二个工作流的接入)

系统现在有**两个**工作流(digest + brief),所以要知道一次 run 跑哪个:
- 给 Run/Schedule 加一个 **`workflow` 标识**(enum/str);runner 据此 **dispatch** 到 `build_digest` 或 `build_brief`。
- **保持简单 dispatch,不做完整数据化**(那是 Phase 7)。这是过渡桥:既让两个工作流共存,又为 Phase 7 铺垫。
- CLI `run-once` 加 `--workflow digest|brief`(默认 digest,保回归);排程可指定 workflow。
- 迁移:Run/Schedule 表加 `workflow` 列(默认 `digest`,alembic 新迁移)。

---

### 9. 测试(全离线,不设 `ANTHROPIC_API_KEY`)

- `test_sources`(新):RSS 解析(fixture)→ 统一 item;去重;坏 feed 优雅降级。
- `test_brief_orchestrator`(新):注入 fake agents——过滤丢噪音、保留 ≤M;每条摘要 + N 视角;空输入短路;成本上限;`Brief` 组装正确。
- `test_runner`/`test_cli`(扩):`--workflow` dispatch;brief 路径 suspended/completed(若挂 review 另说,本期 brief 默认不挂 checkpoint)。
- `test_db`(扩):`workflow` 列 + 默认 digest。
- 既有测试保持绿(digest 路径不回归)。

---

### 10. 流程(一个 session 内,带 review 闸)

1. 先出 **Unit 1 详细计划 + Unit 2 概要** → 停,等我 review(别直接建)。
2. 过 → 建 Unit 1 → 报告 → 停(我 review + 你本地真抓取验证)。
3. 过 → 细化 Unit 2 → 我确认 → 建 → 报告 → 停(review + 真 E2E:抓取+过滤+多视角+发信)。
4. 过门 → 合并 master。
5. 全程离线测试、不设 key、分支 `phase6`。

---

### 11. 决策点(请确认或否决)

1. **来源清单**用 §4 起始集(全 RSS、X 暂缺)。
2. **视角默认 3 个:商业 / 政策 / 技术**(config 可改、可加)。
3. **过滤后保留 ≤8 条**、每源抓 ≤20、视角 3 —— 成本闸默认值(可调)。
4. **工作流选择器走简单 dispatch + `workflow` 列**(不做数据化,留 Phase 7)。
5. brief **默认不挂人在环 checkpoint**(像 digest 自动跑一路到底);要不要给它加 `--review` 走人在环,你定(默认不加)。
