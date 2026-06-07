# Phase 5 · 余下单元任务简报
## 编排引擎迁到 LangGraph + 人在环 suspend/resume

> 同一个 session 内完成余下两个单元(就是刚合并完 Unit 1 的那个 session)。
> 新分支:`phase5-langgraph`(从最新 master 切)。

---

### 1. 背景与决策

- **决策**:编排引擎用框架(**LangGraph**),系统仍自建。原则——**自己造差异化(系统设计 / agent 组合 / 契约 / 配置模型 / UX),借用通用件(编排引擎的硬 plumbing)**。
- **为什么是现在**:你只手搓了一个简单 orchestrator(Unit 1),切换成本低;趁还没手搓更复杂编排之前迁。
- **本期目标不是改进 digest**(它太简单、一遍就够)。本期是:
  (a) 在**已测过的已知地形**上学会 LangGraph;
  (b) 立起 LangGraph 引擎,为真正想要的 **人在环 suspend/resume** 打地基。

---

### 2. 范围

- **Unit 2 — 迁移**:把现有 digest 编排(总结→审查→重做)**原样**搬到 LangGraph 引擎,**可观察行为不变**,用既有测试 + 真 E2E 验证无回归。
- **Unit 3 — 人在环原语**:在该引擎上落地"中断 → 持久化 → 等输入 → 注入决定后从断点续跑",**引擎 + CLI 层证明**,用真 checkpointer(不是内存里假装)。

**非目标(留给后续 Phase,本期别碰)**:
- 工作流变成数据(definition-driven 通用 orchestrator)
- web 合成器 UI / 人在环的网页化(接前端)
- meta-agent

本期只到"**引擎 + suspend/resume 原语**"。把红线守住,别越界。

---

### 3. 设计约束 / 不变量(硬性)

**保留既有的接缝,别破坏:**
- `llm.py` 仍是**唯一 SDK 调用者**、`tools=[]` 写死。**LangGraph 节点经 `llm.py` 调模型,LangGraph 绝不直接调 SDK**(否则破坏模型无关 seam)。
- `agent.py` 的契约不动:`parse_digest` 仍**从 source 定位 title/link**(模型回显不可信、伪造链接结构上不可能)、`parse_critique`、契约错误类型。
- 管线契约不动:`render / store / email` 不碰。

**Unit 2 必须保住的行为不变量**(迁移前后一致):
- `max_redos` 上限;用尽 → 接受最后一版 + WARNING;
- 审查格式坏 → 重审一次再接受,**绝不假装通过**;
- summarizer 永不产出合法 → `RuntimeError`;
- 成本上限(summarize ≤ 3 / verify ≤ 6)。

**测试离线**:装 `langgraph`,但测试仍 **mock 模型 / LLM seam**(LangGraph 本身离线跑);**不设 `ANTHROPIC_API_KEY`**。

**两个状态存储的边界(Unit 3 关键)**:
- LangGraph **checkpointer 拥有"图执行状态"**(它的 `checkpoints` 等表);
- 既有 **`runs` 表拥有"业务运行记录"**(状态 / 输出);
- 两者用 **id 关联**(把 LangGraph 的 `thread_id` 对应到 run id);
- **同一个 Postgres、不同表**,不要耦合。
- `checkpointer.setup()` 建表当**迁移脚本**跑一次,**不要塞进应用运行时**。

**digest 默认不挂 checkpoint**:6 点自动跑要一路跑完、不停。人在环是给交互式 / 复杂运行的**可选**能力。

---

### 4. 单元拆解(含验收门)

#### Unit 2 — 迁移到 LangGraph
- 用 `StateGraph` 重建控制流:节点 `summarize`、`verify`;**条件边** pass→END / fail→redo(带计数);用尽 → accept-last + WARNING。
- 替换 `orchestrator.py` 的**内部**为图;`runner` 调用处改为 invoke 这个 LangGraph app;`llm.py` / `agent.py` / 管线契约**不动**。
- 既有针对 orchestrator 的测试**改写为针对图**,但**断言同样的行为**(§3 那些不变量);其余测试尽量不动。
- **用 LangGraph 当前文档确认 API**(版本会变,如 `StateGraph`、`add_conditional_edges`、`compile(checkpointer=...)`)。
- **验收门**:全套测试绿(数量可变,但覆盖所有行为不变量);**真 SDK 本地 E2E 一次**(digest 照常产出、入库、发邮件),确认与迁移前可观察行为一致。

#### Unit 3 — 人在环 suspend/resume 原语
- 引入 **checkpointer**:先可用 `InMemorySaver` 把机制跑通,再换 **PostgresSaver**(与 app 同库、不同表);计划里说明这个推进顺序。
- 在图里加一个**可选的 human-review 中断点**:节点内调 `interrupt(...)` 把待审内容抛给人;图暂停、状态由 checkpointer 持久化;对应 run 进入 **`awaiting_input`** 态(`runs` 表加这个状态)。
- 加 **CLI 续跑路径**:
  `cli resume-run <run_id> --decision approve|redo|edit [--feedback "..."]`
  → 用对应 `thread_id` 加载快照、`Command(resume=<decision>)` 从断点续跑到结束。
- **该 checkpoint 可配置开关;digest 默认关**(一路跑完)。
- **验收门**:
  - 测试证明 中断 → 持久化 → **(模拟进程重启 / 重新加载)** → 续跑 → 正确收尾;
  - CLI 走通一次"中断 + resume-run + 收尾";
  - digest 仍能**无 checkpoint** 一路跑完(回归保护);
  - **真 SDK E2E 一次**走完整 human-in-the-loop(触发中断 → `resume-run` → 收尾)。

---

### 5. 风险 / 注意

- LangGraph 是 LangChain 生态、抽象偏重:**只用** `StateGraph` + 条件边 + checkpointer + `interrupt()/Command(resume=)`,别顺手引入生态里其他件。
- **别让 LangGraph 越过 `llm.py` 直接调模型**——这是模型无关 seam 的命根子。
- 两个状态存储别耦合,只用 id 关联。
- 持久化 + 续跑要**真用 checkpointer 验证**(测试里模拟"重新加载快照再续跑"),这是采用 LangGraph 的核心理由,不能只在内存里假装。

---

### 6. 交付物

- 迁移后的引擎 + 节点;`resume-run` CLI;`runs` 表 `awaiting_input` 迁移;测试。
- **两次真 E2E**:Unit 2 无回归 / Unit 3 人在环。
- 更新 `BACKLOG` 与 blueprint 决策记录:记下"**编排引擎 = LangGraph**"、"**人在环 suspend/resume 原语已在引擎层落地**(web 化留后续 phase)"。

---

### 7. 流程(一个 session 内做完两个单元)

1. 先出 **Unit 2 详细计划 + Unit 3 概要** → **停**,等我 review(**别直接开建**)。
2. review 通过 → 建 Unit 2 → 报告 → **停**(我 review + 你本地真 E2E 门)。
3. 过门 → **细化 Unit 3 计划** → 我确认 → 建 Unit 3 → 报告 → **停**(review + E2E 门)。
4. 过门 → 合并到 master。
5. 全程 **离线测试、不设 `ANTHROPIC_API_KEY`、分支 `phase5-langgraph`**。
