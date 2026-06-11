# Phase 10c · 任务简报 — 多 agent 对话(coder + reviewer)

> 状态:**简报待审**。这是路线图最后一块有分量的主线工作(蓝图 §8「10c 多 agent 对话」),
> 也是"系统终点"叙事的收尾。先过目设计 + 决策点,确认后再建。

## 1. 目标:把"单 coder + 人审"升级为"coder + 自动 reviewer 对话 + 人审"

现状(Phase 10a/b):coding 家族是**一个** coder agent 跑一轮 → 人审 diff(approve/redo)。
人是唯一的审查者。

10c:在 coder 和人审之间插入一个**自动 reviewer agent**。它读 coder 的 diff + 任务,判断
"够好了"还是"要改",要改就把**具体反馈**喂回 coder 再跑一轮——**有界轮次**内 coder↔reviewer
来回"对话",收敛后才把结果交给人。这就是两个 agent 协作:coder 写、reviewer 评、coder 改。

**为什么是 coder+reviewer**:这是"多 agent 对话"最扎实、最有用的实例(自动代码评审,人看到的已是
被一个 AI 评审员过滤、打磨过的结果),而且**完全复用现有模式**:
- digest 家族已有 summarizer→verifier→有界重做 的先例(行为同构)。
- coding seam 已支持 `feedback`(human redo 把反馈附到任务上)——reviewer 的反馈走同一条路。
- reviewer **只读 diff、不需要工具**,所以走 `llm.py` 裸模型接缝(不是 coding seam),无沙箱/安全新面。

## 2. 范围(做 / 不做)

**做**:
- 新 reviewer agent(`coding_reviewer`,走 llm 接缝):输入 = 任务 + diff + 变更文件 + 命令;
  输出 = 严格 JSON `{"approved": bool, "issues": [{"severity", "detail"}], "summary": str}`,
  严格解析(AgentContractError → 计入有界,容错降级)。
- coding 图新增 `review` 节点(在 coding 与 finalize_gate 之间)+ 路由:approved → finalize_gate;
  needs-work 且还有轮次 → 回 coding(带 reviewer 反馈);轮次用尽 → finalize_gate(附"未收敛"标记,
  交人审兜底)。轮次上界 = WorkflowDef 数据(`max_review_rounds`,默认 2),和 digest 的 max_redos 同构。
- reviewer 判定 + 轮次进 `CodingResult`(`review_rounds`、`review_verdict`、最后一轮 `issues`),
  并入 Phase 11 的 `runs.meta`(verdict) + 审核 payload + RunDetail(人审时看到"AI 评审员说了什么")。
- 离线测试:reviewer parser、图三态(一轮过 / 改一轮再过 / 用尽不收敛)、与人审门的衔接、
  既有 coding 测试逐字节绿(reviewer 默认注入 fake,不碰真 SDK)。

**不做(本期)**:
- N>2 个 agent / 自由编排的 agent team / 协调器——先把 2-agent 对话做扎实,多 agent 编排留后。
- reviewer 用工具自己跑测试验证(它只读 diff)——要"跑测试再评"是 coder 的活,reviewer 看结果。
- 改 coder 的沙箱/安全模型——reviewer 无工具、无沙箱面,coder 的 10b 安全全留。
- 把 reviewer 提示词 DB 化做成可合成的 AgentDef——reviewer 是 coding 家族的代码接缝(照 coder/coding_fn
  先例,coding 家族不进 manifest);要让它可合成是另一回事。

## 3. 决策点(请定夺)

- **A · 对话形态 = coder + 单 reviewer,有界来回**(推荐)。最小、最有用、最复用。
  备选:更完整的 agent team(多角色 + 协调器)——大得多,且对"写代码"这个任务收益递减。**默认按 A。**
- **B · reviewer 走 llm 裸接缝、只读 diff**(推荐,无新安全面)。备选:给 reviewer 工具让它独立验证
  ——引入第二个 sandboxed agent,安全/计费翻倍,收益不清。**默认按 B。**
- **C · 自动 reviewer 与人审门的关系**:reviewer 在前(自动收敛)、人审在后(最终拍板)。
  即 coder↔reviewer 先跑到收敛或用尽,再(可选)进人审。**默认按 C**(人始终是最后一道)。
- **D · 默认开关**:reviewer 默认**开**(coding 家族行为变化:多了自动评审轮)还是**opt-in**
  (新 workflow param,默认关 → 既有 coding 行为逐字节不变)?**倾向 opt-in 默认关**,保无回归 +
  让你显式选择;但若你要"coding 从此自带评审",可默认开。**这一条我想听你的。**
- **E · reviewer 模型**:复用 cfg.model(和 coder 同模型)还是允许 WorkflowDef 指定(如 reviewer 用更强模型)?
  **默认复用 cfg.model**(per-agent model 是另一条延后的债,不在本期捆绑)。

## 4. 判官 / 验收

- 既有 ~470 pytest + ~53 vitest 逐字节绿(reviewer 默认 fake 注入,coding 家族离线跑通)。
- 新离线测试覆盖 §2。
- **真 E2E + 合并 = 你 hands-on**(coding 涉真 shell,沿用 10b 纪律:本期 session 全程离线 + mock、
  不设 ANTHROPIC_API_KEY、不跑真 SDK、不合并)。给你的 E2E:一个会触发 reviewer 打回的任务
  (如"加函数但故意留个明显问题"),看 coder↔reviewer 来回收敛、人审时看到评审意见。

## 5. 红线(不变)

- `coding_agent.py` 仍是唯一 agent-loop SDK 调用者;reviewer 走 `llm.py`(唯一裸模型调用者),两接缝纪律不变。
- coder 的 10b 安全全留(沙箱、命令审计、.git 兜底、密钥擦除、clean-tree、人审)。
- 绝不设 `ANTHROPIC_API_KEY`;订阅认证。
- web 层零 SDK/langgraph;新 reviewer 模块进 test_api_no_sdk 禁入集。
- 既有测试逐字节绿是无回归判官。

## 6. 与北极星的关系

蓝图把 coding agent(Phase 10)列为"系统终点",10c 是其收尾:从"一个 agent + 人"到"一队 agent 协作 + 人
掌舵"。这也把 §10 学习地图的"多 agent 编排(粘合剂)"在 coding 语境里再走一遍——digest 的 summarizer+verifier
是推理任务的双 agent,coding 的 coder+reviewer 是工具任务的双 agent,两者同构,印证了通用编排器的价值。
