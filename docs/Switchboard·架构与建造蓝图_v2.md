# Switchboard · 架构与建造蓝图 (v2)

> **个人 AI 代理工作流控制平台。这份文档是整个系统的"单一真相来源 / 北极星"。**
> 用途:(1) 描述系统的完整目标架构;(2) 作为发给每个 build session 的总图,对齐同一套设计与契约。
> **你(人)是 orchestrator**;这份文档 + 里面定义的契约,就是协调多个 build session 的"胶水"。
> 原则:**北极星 = 完整愿景,给方向;实现一寸寸来(见第 8 节路线图)。**
> 状态约定:✅ 完成 · 🔨 进行中 · ⬜ 未开始。

---

## 1. 系统目标与范围(阶段 0)

**一句话**:一个**自托管、登录即用的控制台**,用来运行、排程、监控和管理我的个人多 agent 工作流系统。(*托管:本机 Mac 自托管;上云推迟——见决策 4。*)

**完整能力(愿景)**:任何设备打开网页、账号密码登录,就能——
- 运行 / 排程工作流
- 查看任务状态与产出
- 安排新任务、处理系统相关事务、监控运行
- **人在环掌舵**:运行可在关键步骤暂停、给我审核,我给方向(通过 / 重做 + 反馈)后从断点续跑
- **工作区**:工作流针对一个选定的本地资料夹运行,产出落在该资料夹,代理被限制在工作区内(权限)
- 设备自适应:**电脑 / 平板 → 编排工作流(重操作);手机 → 监控等轻操作**

**落地工作流**:
- MVP 锚点:每天定时抓取指定来源新闻 → 解析 → 推送给我。(✅)
- 价值验证:一个**真实复杂任务**——多源采集 + 过滤 + 解读 + 逐条反向思考。(✅ Phase 6 的 brief 工作流)简单简报看不出多代理价值,这个任务才证明它。

**学习目标**:借这个项目练 agent 工程(记忆、权限、工作区、编排)+ 走一遍软件工程四阶段。

**明确延后 / 非目标**:meta-agent(最后做)· 浏览器 / 社媒 agent(后期,先过 ToS)· 重度多 agent 并行(后期)· 上云(推迟)。

---

## 2. 北极星架构(阶段 2)

系统劈成三个面,**核心是"控制面"与"执行面"解耦,中间靠数据面传话**。

```
          ┌─────────────────────────────────────────────┐
  你 ───▶ │  控制面 Control Plane(你登录的网页)         │
 任意设备 │   React 前端  ──REST/OpenAPI──▶  FastAPI 后端 │
          │   (认证后:看状态/排任务/看产出/监控/人在环)  │
          └───────────────┬─────────────────────────────┘
                          │  读写"定义/排程/状态/产出"
                          ▼
          ┌─────────────────────────────────────────────┐
          │  数据面 Data Plane = 单一真相来源             │
          │   PostgreSQL(runs/outputs/users + LangGraph  │
          │   checkpoints,同库不同表)                   │
          └───────────────┬─────────────────────────────┘
                          │  worker 领取待执行的 run、写回状态/产出
                          ▼
          ┌─────────────────────────────────────────────┐
          │  执行面 Worker Plane(真正干活的 agents)     │
          │   Scheduler(定时)→ Runner → Orchestrator   │
          │   (LangGraph 引擎 + 人在环)→ llm.py 模型接缝 │
          │   ├─ 调外部:新闻源、邮件                     │
          │   └─ 后期:多源/反向思考、子 agent、浏览器     │
          └─────────────────────────────────────────────┘
```

**关键解耦原则**:网页**不直接跑 agent**;网页把"任务定义 / 排程"写进 DB → worker 从 DB 领取并执行 → 把状态和产出写回 DB → 网页再读出来显示。

**为什么必须这样**:工作流是长时间、定时的,塞不进一个网页请求;网页要随时响应;agent 要能没人看着也继续跑。**这条解耦就是系统的脊椎**,也是控制面与执行面之间的契约。第一天就定死。(✅ 经 DB pending-run 认领实现)

---

## 3. 核心设计:定义即"一等数据" (✅ Phase 7 数据化 · Phase 8 入库)

**把"agent 定义""工作流定义""排程"做成系统里的一等数据(存 DB),不要写死在代码里。**

- ✅ Phase 7:定义成为声明式数据;**通用编排器**读定义 → 动态构图 → 在 LangGraph 上跑(digest + brief 已 dogfood 重写)。
- ✅ Phase 8:定义入 DB(`workflow_defs`/`agent_defs`,DB 覆盖优先、代码内置兜底、内置只读)+ 两道护栏验证 + 网页合成器 CRUD。
- 以后:meta-agent 不过是**同一份定义数据的另一个写入者**(人 / UI / meta-agent 都往同一张表写)。

这是为未来 meta-agent **现在就留好的缝**:声明式定义 → 谁创建都行。*注:从 ≥2 个真实工作流(digest + 复杂任务)归纳才好定 schema,避免过早抽象——故 Phase 6 先于 Phase 7。*

---

## 4. 数据模型草案(DB 契约 · 概念层)

| 实体 | 作用 | 状态 |
|---|---|---|
| **User** | 认证(id、账号、密码哈希、角色) | ✅ |
| **Run**(Task) | 一次执行(id、关联工作流、状态、起止、触发方式) | ✅ 状态含 pending/running/success/failed/**awaiting_input** |
| **Output**(Artifact) | 产出(id、关联 Run、类型、内容) | ✅ |
| **Schedule** | 排程(id、关联工作流、cron、启用) | ✅ |
| **AgentDef** | agent 定义(系统提示、允许工具、权限、**provider/model**) | ✅ Phase 7 数据化、Phase 8 入 DB |
| **WorkflowDef** | 工作流定义(步骤序列、每步用哪个 AgentDef、参数) | ✅ Phase 7 数据化、Phase 8 入 DB |
| **Event**(Log) | 审计 / 监控 | ✅ Phase 11:`run_node_events` 表(每节点 running/done/failed/awaiting),驱动实时工作流图;更细的审计 Event 仍可后补 |
| **Memory** | 跨 run 长期记忆(后期 pgvector) | ⬜ 推迟(决策 6) |
| **(LangGraph)checkpoints** | 图执行状态(suspend/resume 用) | ✅ 与上表同库不同表、无 FK,以 `thread_id == run_id` 关联 |

关系:WorkflowDef 引用多个 AgentDef;Schedule 指向 WorkflowDef;每次触发产生一个 Run;Run 产生 Output 和 Event;Memory 跨 Run 持久。

---

## 5. 关键契约 / 边界(✅ 已实现)

1. **控制面 ↔ 执行面(经 DB)**:UI 只写"定义 / 排程"、读"状态 / 产出";worker 只读"待执行 Run",执行后写回。两边不直接调用对方。(经 `create_run(pending)` + 原子 `claim_next_pending_run` 实现)
2. **前端 ↔ 后端(REST / OpenAPI)**:FastAPI 自动生成 OpenAPI = 契约本身。资源面:`auth`、`runs`、`outputs`、`schedules`(`workflows`、`agents` 待 Phase 7)。
3. **后端/worker ↔ Agent 运行时**:worker 拿工作流 → Orchestrator(LangGraph)编排 → 节点经 `llm.py` 调模型 → 拿回结构化结果。产出契约 = `Digest(title / link / one_line_summary)`,且 **title/link 从来源定位、非模型回填**。

---

## 5.5 模型 / Provider 抽象(已定型为"LangGraph 引擎 + 单一模型接缝")

目标:系统不绑死某一家(模型无关工程)。**当前定型(决策 8)**:

- **编排引擎 = LangGraph**(系统自建、引擎借用);所有控制流走 LangGraph 的 StateGraph。
- **模型调用经 `llm.py` 单一接缝**:推理节点用 Claude Agent SDK、`tools=[]`(即裸模型调用,无 agent harness)。换模型/供应商只动这一处。
- **(Phase 10a 起)coding 家族经第二接缝 `coding_agent.py`** 跨过 `tools=[]` 边界:唯一的 agent-loop SDK 调用者、可注入 fake、有界循环、沙箱 containment(见决策 13)。两接缝纪律相同:SDK 不外漏。
- **多厂商仍只留缝、用时再接**:真要接非 Claude(或要完整 agentic harness)时,再在 `llm.py` 后面加第二条实现;`AgentDef` 的 `provider/model` 字段(Phase 7)是路由依据。

**原 A/B 岔路的结论**:倾向"全走 LangGraph 编排(Claude 也走),provider 藏在 `llm.py` 之后"。**YAGNI 不变**:先单路径跑通,缝留好,用时再加。

---

## 6. 技术栈(含理由 / 现状)

- **后端:FastAPI(Python)** ✅ — Agent SDK 的 Python 版一等公民;异步适合 I/O 密集;自动 OpenAPI。
- **数据库:PostgreSQL** ✅ — 关系可靠;JSON 存声明式定义;后期 pgvector 做向量记忆。
- **前端:React + Vite + TypeScript** ✅ — 控制面 UI;响应式覆盖电脑/平板/手机。
- **调度器:APScheduler** ✅ — 60s 轮询 + DB pending-run 认领(决策 1)。
- **认证:自托管轻量方案** ✅ — passlib/bcrypt + Starlette SessionMiddleware 签名 cookie + CSRF token(决策 3;比 fastapi-users/Auth0 更轻,单用户够用);全程 HTTPS、bcrypt 哈希。
- **编排引擎:LangGraph** ✅ — StateGraph + checkpointer(决策 8);已合并 master,通用编排器在其上构图。
- **Agent 运行时:Claude Agent SDK(Python)** ✅ — 推理经 `llm.py`、coding 经 `coding_agent.py` 两接缝调用;计费走订阅额度(绝不设 `ANTHROPIC_API_KEY`)。
- **迁移:Alembic** ✅;**测试:pytest 454 项 + 前端 vitest 42 项**(全离线确定性)✅。
- **多厂商层(后期才接):LiteLLM / LangGraph 其他 provider** — 见 §5.5,先留缝。
- **托管:本机 Mac 自托管** 🔨,上云推迟(决策 4)。

---

## 7. 安全与权限(横切质量带)

- **认证** ✅:登录是真实安全面(决策 3 自托管方案)。
- **密钥管理**:API key、SMTP 应用密码等**绝不写死、绝不入版本库**;用环境变量 / `.env`(`.gitignore`)。*推 GitHub 前务必扫历史,泄露则轮换。*
- **Agent 权限** ⬜:用 Agent SDK 权限模式,**逐 agent 最小权限**(写进 AgentDef,Phase 7);**工作区限制**(代理只能碰选定资料夹,Phase 8)。
- **meta-agent 护栏(Phase 9)** ✅:能创建 agent 又能排程的 agent = 能让系统干任意事。已配齐:调色板硬边界(只产出重组已注册组件的**可审数据**,永不产代码)、**人在回路审批必经**(无审门路径被 worker 拒绝)、只新不改 + 落盘前重验、审计 Output(请求/提案/验证报告/决定全程入库)。

---

## 8. 增量路线图(建造顺序)

愿景当北极星,一片片建。每阶段都是"能跑、能学到一个概念"的切片。

- ✅ **Phase 0** — 契约定稿
- ✅ **Phase 1** — 后端单工作流端到端(新闻简报:抓取 → 解析 → 产出入库)
- ✅ **Phase 2** — DB 真相来源 + 调度器 + 邮件通知(定时触发、推送)
- ✅ **Phase 3** — 控制面 web app(认证 + 看状态/产出 + 排程 + 立即跑;响应式 —— **原 Phase 4 已并入此**)
- ✅ **Phase 5** — 多 agent 编排 + LangGraph 引擎 + 人在环(U1 多代理有界重做 / U2 迁 LangGraph 行为不变 / U3 suspend-resume 原语),全部合并 master
- ✅ **Phase 6** — 真实复杂任务工作流(多源采集+过滤 / 解读+反向思考 / 组装+E2E,约 2–3 unit)。证明多代理价值;给出第二个真实工作流供 Phase 7 归纳。
- ✅ **Phase 7** — 工作流与代理"数据化" + 通用编排器(代理即数据 / 工作流定义 schema + 读定义动态构图的通用编排器 / dogfood 重写两个工作流,约 3 unit)。兑现第 3 节。
- ✅ **Phase 8** — 网页合成器 + 监控 + 网页版人在环 + 工作区(监控+网页人在环 / 合成器 UI / 工作区选择+权限,约 2–3 unit)。
- ✅ **Phase 9** — Meta-agent(带护栏),2026-06-12 合并:meta 工作流家族(请求 → llm 接缝起草 WorkflowDef/AgentDef 提案 → 确定性验证节点有界重画 → 人审门 → **approve 才落盘**,经现有 defs CRUD)。护栏全配:调色板硬边界(meta/coding 胶水不进 manifest,不可自我起草)、必审(straight 路径拒绝)、只新不改 + 落盘前重验(worker 侧内置只读守卫)、审计 Output。U1:保存验证的 agents 命名空间扩为"内置 ∪ DB AgentDef"。真 E2E 全环:meta 起草 cautious-digest → 人工 redo 补输出契约 → approve 落盘 → **运行了 meta 创造的工作流(成功)**。详见 Phase 9 简报。
- 🔨 **Phase 10** — Coding-agent(**系统终点**),分片推进:
  - ✅ **10a** 接缝 + 编码家族:`coding_agent.py`(唯一 agent-loop SDK 调用者、可 fake、有界)+ coding 工作流 + web diff 审核
  - ✅ **10b-1** 可指挥:每 run task/workspace + git 感知 diff/还原 + clean-tree 前置 + `.git` 拒写
  - ✅ **10b-2-1** 真 shell + 沙箱(2026-06-11 合并):Bash + 借用 Seatbelt(文件系统→工作区、网络拒、命令超时)+ 命令审计 + `.git` 完整性兜底 + worker 密钥 denylist 擦除(逃逸验收 hands-on 通过)
  - ✅ **10c** 多 agent 对话(coder + 自动 reviewer):coder 与一个走 llm 接缝(只读 diff、无工具、无新安全面)的自动 reviewer **有界来回**(`max_review_rounds`,默认 opt-in 关 → 运行时逐字节不变);reviewer approve 或给反馈让 coder 再改,收敛后人审最后拍板。verdict 进 `runs.meta` + 审核 payload。496 测试,40-agent 对抗评审 0 缺陷。真 E2E + 合并待用户。
  - ✅ **10b-2 后段(commit 自动化)**:成功/批准的 coding run 由 worker 在沙箱外 `git_commit`(opt-in `CODING_AUTO_COMMIT` 默认关 → 逐字节不变;只 stage agent 改的文件,不 `add -A`,审核挂起期间用户改的别的文件绝不混入;best-effort 永不拖垮 run;`--no-verify`、列表式无注入;短哈希进 runs.meta + RunDetail)。29-agent 对抗评审抓到并修了"resume-approve 误提交用户文件"的 BLOCKER。**PR/push 延后**(需 GitHub 认证+网络+对外发布)。503 测试。
  - ✅ **env 擦除子进程级**(原前置债已清,2026-06-12):`_scrubbed_env()`(进程级 pop/restore os.environ,靠单串行 worker 撑)→ `_secret_overlay()`(只读 os.environ,把密钥覆写为 `""`,经 options.env 注入 → SDK `{**os.environ, **options.env}` 合并使子进程见空值,绝不动共享 env)。并发安全不再依赖单 worker 不变量;同 denylist,覆盖零变化。28-agent 评审 0 缺陷 + 真 SDK E2E(植入假密钥+保留真 DATABASE_URL,沙箱 agent 回显 `SMTP=[] SECRET=[] DB=[]` 全空且 run 成功=订阅认证未坏)。503 测试。
  - ✅ **网络细粒度放行**(2026-06-12):coding 沙箱从全网络拒 → 可选 `CODING_ALLOWED_DOMAINS`(运维 env,逗号分隔)→ `sandbox.network.allowedDomains`。**默认空=逐字节全拒不变**;白名单**排他**(只放行列出的域,其余仍拒)。**安全边界**:只来自运维 Config,绝不来自 WorkflowDef params(否则可编辑数据能拓宽出口=提权);和 env 擦除互补(出口开了但沙箱里没密钥可外泄)。24-agent 评审 0 缺陷 + 真 SDK E2E(白名单=example.com:`ALLOWED CODE=200` / `DENIED 403 BLOCKED`)。509 测试。
  - ⬜ 10b-2 后段余项(push/PR——用户暂不需要)· 10b-3(会话生命周期 UI)· Agents 页主从布局(可选 UI)
- 🔨 **(小项,非 phase)Mac-as-server**:已建(deploy/ 的 launchd user agents:worker 带 caffeinate + API 于 127.0.0.1:8400,Tailscale 远程方案见 deploy/README)——**安装 = 用户跑一次 `deploy/install.sh`**(自启动服务的开关留给人)。上云推迟。
- ✅ **Phase 11(可观测性)** — 工作流可视化 + 运行时监控:每个工作流可展开看**拓扑图**(节点/边/分支/循环,前端手绘 SVG);运行时引擎逐节点发事件(`run_node_events`,opt-in 经 contextvar、离线无影响),`GET /runs/:id/progress` 暴露,RunDetail 用同一张图**实时点亮**节点(running/done/failed,跑动时 ● live)。兑现决策表 Event 行 + 北极星的"监控运行"。
- (浏览器 / 社媒 agent:按需插入,先过 ToS 合规。)

诀窍不变:**缝按完整愿景设计好,但一片片实现。**

---

## 9. 怎么协调 build sessions

- **你是 orchestrator**,持有全局(这份文档)。别指望 session 自己组织。
- **契约先行,再开工**;greenfield 阶段别并行撒一堆 agent。
- **先用一个 session 顺序做通纵向切片**,拿到能跑的骨架 + 验证契约。
- **Git 仓库 = 单一真相来源**(代码 + 本规约都在里面);分支 / worktree 防互踩。
- **集成验证**:契约测试 + 集成测试 + 真实 E2E 门 + 无回归纪律(迁移时旧测试原样绿)。
- **你来 review 和整合**(orchestrator + verifier)。
- 给每个 session 的料:**本文档 + 它那块的具体契约 + 小而清晰的范围**。

---

## 10. 学习地图(对回软件工程框架)

| 系统部分 | 练到的东西 |
|---|---|
| 定 MVP、砍范围、定义工作流 | 阶段 0(价值与需求) |
| 好代码 / Git / 测试 / 调试 | 阶段 1(基本功) |
| 控制面/执行面解耦、契约、定义即数据 | 阶段 2(设计与架构) |
| 托管、调度、监控、告警 | 阶段 3(上线与演进) |
| 认证、密钥、agent 权限、工作区、meta-agent 护栏 | 安全 + 权限(质量带) |
| 跨 run 记忆、状态持久化、suspend/resume | 记忆 / 状态管理 |
| subagent、orchestrator、契约测试、人在环 | 多 agent 编排(粘合剂) |

---

## 附录 · 决策记录

| # | 决策 | 结论 | 备注 |
|---|---|---|---|
| 1 | 调度器 | APScheduler 轮询(60s)+ DB pending-run 认领 | DB 驱动、动态生效;手动触发经 pending-run 交给 worker |
| 2 | 通知渠道 | 邮件(SMTP) | 未配置静默跳过、失败优雅降级;不做 Telegram。*投递可观测性为待补项(BACKLOG)* |
| 3 | 认证 | 自托管:passlib/bcrypt + SessionMiddleware 签名 cookie + CSRF token | 单用户;未用 fastapi-users |
| 4 | 托管 | **本机 Mac 自托管;上云推迟** | Mac-as-server 为小项(非 phase);到上云那步再定 VPS vs PaaS |
| 5 | 新闻源 + 解析 | RSS(目前 Hacker News) | 产出契约 = `Digest(title / link / one_line_summary)`,title/link 从来源定位 |
| 6 | pgvector 长期记忆 | 推迟 | 待长期记忆需求出现再引入 |
| 7 | 前端最小范围 | 登录 + 运行看板 + 详情/产出 + 排程 CRUD + 立即跑;响应式 | 见 Phase 3 Unit 2 简报 |
| 8 | 编排引擎 | **LangGraph**(系统自建、引擎借用) | StateGraph;节点经 `llm.py` 调模型、不直连 SDK;web 层不加载 langgraph(测试守卫);digest 默认不挂 checkpoint。**已合并 master** |
| 9 | 人在环 suspend/resume | `interrupt()` + checkpointer + `resume-run` CLI | `thread_id == run_id`;PostgresSaver 与 `runs` 同库不同表、无 FK;dict-state(避开 dataclass 易碎序列化);web 化已在 Phase 8 落地。**已合并 master** |
| 10 | 项目命名 | **Switchboard**(repo `switchboard`) | 内部 `news_digest` 包暂不改名(报告后再重构);叙事:Switchboard 平台,news_digest 是其首个工作流 |
| 11 | 路线图原则 | 复杂任务(Phase 6)先于数据化(Phase 7) | 从 ≥2 个真实工作流归纳通用 schema,避免过早抽象 |
| 12 | 构建 vs 买 | 自造差异化、借用通用件 | 编排引擎用 LangGraph(通用、难、已解决的 plumbing);系统设计/契约/配置模型/合成器/meta-agent 自建(差异化 + 学习价值) |

| 13 | 系统终点 | **Switchboard 里的 coding agent(Phase 10)** | 跑会用工具、管会话、能互相对话的 coding agent。实现=新增 `coding_agent.py` 接缝(Agent SDK agent-loop)+ 会话管理 + 沙箱 + 多 agent 协调;激活已留好的 AgentDef.允许工具 + §6 的 Agent SDK 权限/工作区/沙箱。正交于 8/9(依赖 Phase 7 + run/worker 管道,不依赖合成器/meta-agent),放最后是风险排序(最大/最险/计量计费),非依赖 |
| 14 | Phase 8 通用性约束 | 合成器与数据模型保持节点类型开放 | 将来加 `coding_agent` 节点类型是"加"不是"重写"(Phase 7 的 α 已支持);别写死"只有推理工作流"假设。Phase 8 的监控 + web 人在环为 coding agent 直接复用 |

> 状态以代码 + 各阶段简报为准(本表只记"定了什么")。当前:Phase 0–9 + 10a/10b-1/10b-2-1 已合并 master(Phase 9 含真 E2E:meta 起草 → 人审 redo/approve → 运行其造物成功);Phase 10 余下分片(10b-2 后段/10b-3/10c)未开始 —— 详见 §8 路线图。