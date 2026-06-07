# Phase 5 · Unit 1 任务简报:多 agent 编排(orchestrator + 总结 + 审查)

> **给执行的 build session:** 这是 Phase 5(多 agent)的第一块。Phases 1–3 已完成并在 `master`(单 agent 管线、DB、调度器、runner、邮件、web 控制面)。现在的"总结"是**一次** Agent SDK 调用;这个 unit 把它升级成**一个被编排的多 agent 子流程**。完整设计见 `docs/` 的《架构蓝图》。
> **不要破坏 Phases 1–3 的模块与测试。** 人类是 orchestrator;范围外别自作主张,有歧义问人。

---

## 1. 目标(一句话)

把现在单步的"总结"升级成**由一个 orchestrator 编排的多 agent 子流程**:orchestrator 协调 **总结 agent** 和 **审查 agent(verifier)**——总结产出 digest,审查对着**源条目**核对质量,不过关就把反馈给总结、让它重做(**有上限**)。最终产出**与现在同样的 Digest 契约**,所以管线其余部分(渲染/入库/邮件)**不动**。

## 2. 关键设计点(计划里务必说清楚)

**① agent 之间的契约 —— 多 agent 的"胶水",也是这个 unit 的核心学习点**
每个 agent 的输入/输出 schema 写明白(这就是接缝):
- **总结 agent**:输入 = 抓取到的条目;输出 = 结构化 Digest(`title / link / one_line_summary`)。
- **审查 agent**:输入 = Digest **+ 源条目(用于核对,不是凭感觉)**;输出 = Critique(`passed: bool` + 具体问题列表,如"第 3 条总结与原文不符""某 link 不在源里")。
> 审查必须**对着源核对**(查幻觉/失真/编造链接),否则就是走过场。

**② orchestrator 的控制流 + 有界重做循环**
- 流程:总结 → 审查 → **过**则结束;**不过**则把 Critique 反馈给总结、重做。
- **重做有上限(如 2 次)**:到顶仍不过 → **接受最后一版 + 记日志**(LLM 审查本身也会错/永不满意,所以靠上限兜底,绝不无限循环/无限烧额度)。
- orchestrator 是**普通代码**(确定性控制流),**不是让 LLM 自己决定流程**——可预测、可测、可调试。(LLM 当 orchestrator 是 meta-agent,Phase 6 的事。)

**③ 保持既有管线契约不变**
orchestrator 最终输出的 Digest 与现在 `agent.summarize` 的输出契约**一致** → runner / render / store / email **不改**、既有测试保持绿。改动局限在"总结这一步变成了子流程"。

## 3. 范围

**做(In):** 代码 orchestrator + 总结 agent(可复用/改造现有 summarize)+ 审查 agent;agent 间**显式契约 + 输出校验**;有界重做;离线测试(mock LLM);runner 改为调 orchestrator(替换原单步 summarize 调用)。

**不做(Out):** ❌ 更多 agent(多源/排序 → 后续 unit) ❌ LLM 当 orchestrator(meta-agent,Phase 6) ❌ 混合模型路由(先都用 Claude) ❌ 改前端/部署。

## 4. 决策(已定)

- **第一版 agent 组合**:总结 + 审查(verifier)。
- **orchestrator**:自己写的**轻量代码 orchestrator**(确定性;不用 SDK subagent;目的是把契约/handoff/循环学透)。
- **模型**:全部用 Claude(混合路由推迟;但**在 agent 调用处留一个干净的接缝**,以后换模型/加路由不用动 orchestrator)。
- 总结 agent 复用现有 `agent.summarize` 的 `tools=[]` 用法(纯模型调用,不给工具)。

## 5. 技术约束

- 沿用 Claude Agent SDK(programmatic);每个 agent = 构造 prompt → 调 SDK → 解析并**对着自己的契约 schema 校验**输出。
- agent 的结构化输出**必须校验**:schema 不符 → 视为该 agent 失败、交给 orchestrator 处理,**绝不把脏数据往下传**。
- **重做有上限**;每次 agent 调用都消耗 SDK 额度 → 循环上限就是成本上限,务必有界。
- 不破坏 Phases 1–3 的模块与测试;**不设 `ANTHROPIC_API_KEY`**;凭据走 env。
- 离线测试 **mock 掉 LLM**(不连网/不真调 SDK),与现有测试同一路子。

## 6. 工程规范

- 在新分支 `phase5` 上小步提交。
- 测试:orchestrator(过 / 不过→重做→过 / 到顶接受最后一版)、契约校验(各 agent I/O 合 schema、不合时如何处理)、有界循环;**全离线**。不破坏既有测试。
- README 更新(多 agent 子流程怎么跑、两个 agent 的契约、重做上限)。

## 7. 验收标准

1. orchestrator 跑通:总结 → 审查 → 过 → 产出 Digest;契约不符或审查不过 → 重做;到上限 → 接受最后一版 + 记日志。
2. 产出 Digest 与**原契约一致** → runner/render/store/email 不变、既有测试仍绿。
3. agent 间契约**显式**、输出**有校验**;脏输出不会静默往下传。
4. 重做**有界**(不会无限循环/无限烧额度)。
5. 离线测试通过 + README 更新 + 已提交。

> 真 agent 跑通的端到端(真 SDK 真审查真重做)= **你本地验收那一步**,同 Phase 2/3。

## 8. 交付物

- orchestrator + 总结 agent + 审查 agent(+ 它们的契约 schema)
- runner 接上 orchestrator(替换原单步)
- 离线测试
- README 更新

---

> **先给我实现计划**——重点说清:① 两个 agent 的契约 schema;② orchestrator 控制流 + 重做上限与到顶后的行为;③ 怎么保证既有 Digest 契约/管线不变。我确认后再写;做完暂停等我 review。
