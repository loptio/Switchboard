# Phase 10a 任务简报 —— Coding-agent 接缝 + 编码家族(单 agent 端到端)

> 北极星(蓝图决策 13 / Phase 10):在 Switchboard 里跑会用工具、对世界动手的 coding agent。10a = **用最小的真东西证明这次跨界**:一个会用工具的 coding agent,挂在一道**新的接缝**后面,当成一个新"家族",从现有控制平面端到端跑通、产出真实改动、人工审过 diff。**不做**多 agent 对话、完整沙箱、会话 UI、真 shell——那些是 10b/10c。
>
> 这是整套系统第一次**跨过 `tools=[]` 那条它刻意围着建的边界**:从"文本进文本出的推理流水线"变成"会动手的 agent"。所以接缝纪律、离线 mock、有界循环、工作区禁锢是这一关的脊梁。

---

## 1. 核心变化:第二道接缝(coding-agent seam)

现在 `llm.py` 是唯一的 SDK 调用者、写死 `tools=[]`、单次请求→响应。coding agent 需要**工具 + agent 循环**(推理→调工具→观察→再推理,直到完成)。所以新增**一道平行接缝**,镜像 `llm.py` 的纪律:

- **`coding_agent.py`(暂名)= 唯一的 Agent SDK 调用者**,可换、可注入 fake。签名约 `run_coding_agent(task, workspace, *, tools, max_turns, ...) -> CodingResult`(`{summary, diff, changed_files, status}`)。
- **可注入 fake(命根子)**:像 `llm.py` 的 fake 一样,给定 task+workspace **确定性地**"改"文件 / 返回固定 diff/summary。→ **整个编码家族在测试里离线跑通,不碰真 SDK、不设 key、不花钱**。真 Agent SDK **只在你本人真 E2E 时跑**(计量计费)。
- **有界循环(强制)**:max_turns / 工具调用上限 / 预算,**超了就停 + 标 needs-review/失败**,绝不放任无界烧钱(那次 $1800,现在一个 agent 的循环也能烧)。

## 2. 编码家族(新 family,代码;经通用引擎)

Phase 8 的 family 固定 state schema + intake + delivery(代码、runner 驱动)。coding agent = **一个新 family**(正是 Phase 8 FLAG 2 预告的"新形状 = 新代码")。落地:

- **新 node-kind `coding_agent`**(兑现 Phase 8 决策 F:加类型是"加"不是"重写")。它的 handler(按名注册的代码组件)调上面那道接缝、在工作区里跑 agent 循环。**通用引擎照常编译它**(引擎不懂 agent 循环——循环在接缝里;引擎只当它是个会跑 handler 的节点)。引擎改动极小。
- **编码家族 WorkflowDef**(最小):`coding_agent` 节点 →(10a U2 加)`human_review` 审 diff → 结束。
- **state**:task / workspace 路径 / 结果(diff+summary+changed_files)/ status。**intake** = 一个指定的**工作区目录**(非 git clone/worktree——那是 10b)。**delivery** = 把 diff+summary 存成 Output(RunDetail 可看);摘要可复用现有邮件投递。runner 按 `output_ref` 选编码 harness(同 digest/brief)。

## 3. 审 diff 的人在环(复用 Phase 8 的 web-HITL)

coding agent 无人值守地改文件是实打实的攻击面,所以**审 diff 是 10a 的安全网,不是可选**:`coding_agent`(提出改动)→ `human_review`(网页看 diff)→ **批准**(接受=成功)/ **打回**(带反馈→有界重跑)。直接复用 Phase 8 的 `start_review_run`/`resume`/RunDetail 审核面板;载荷从"待审候选"换成"diff+summary"。**零新机制,正好把 5/8 两关的人在环用在刀刃上。**

## 4. 范围(做 / 不做)

**做(10a)**:coding-agent 接缝(唯一 SDK 调用者 + 可注入 fake + 有界循环);编码 family(state/intake=工作区/delivery=diff+summary)+ `coding_agent` node-kind;审 diff 的 web 人在环;RunDetail 看 diff。

**不做(留 10b/10c)**:多 agent 互相对话;完整沙箱/容器隔离;git clone/worktree/分支/PR;会话生命周期 UI(起/接/停/列、实时流);**真 shell/命令执行**(10a 工具只在工作区内读写文件,见决策 B);把编码 family 数据化(它是代码,同 digest/brief);逐 agent model 应用(Phase 8 已存字段,运行时仍不应用)。

## 5. 单元拆分(计划过关后一口气建)

- **U1 · 接缝 + 家族 + node-kind + 有界循环(纯离线,mock SDK)**:`coding_agent.py`(可注入 fake + 有界循环);`coding_agent` node-kind 入引擎;编码 family 的 state/harness 入 runner(按 output_ref 选);一个编码 WorkflowDef。**判官**:既有 323 测试**原样绿**(新 family/node-kind 纯增量,digest/brief/HITL 不动)+ 新机器离线测试(fake 接缝跑通一个编码 run、产出 diff+summary 存 Output;有界循环超限即停;`coding_agent` 节点编译)。
- **U2 · 审 diff 人在环 + RunDetail diff 视图**:编码 WorkflowDef 加 `human_review` 审 diff;复用 Phase 8 的 review 启动/resume;RunDetail 显示 diff、批准/打回(+反馈→有界重跑)。**判官**:既有 HITL/web-review 测试原样绿 + 新编码-review 测试(批准→成功、打回→重跑)。
  - **(保险)** 建完 U1 贴一下接缝签名 + `CodingResult` 形状 + 编码 WorkflowDef 形状给我扫一眼;不停、续 U2。

## 6. 决策点(请确认或否决 / 给方向)

- **A · 接缝引擎 = Claude Agent SDK**(`claude-agent-sdk`,Python,自带 agent 循环 + 工具 + 会话),非 subprocess 驱 Claude Code headless。**默认按此**(原生 Python、贴 worker)。
- **B · 10a 工具集 = 仅工作区内读写/编辑文件,无 shell/命令执行**(攻击面最小,先证循环+工具+接缝)。真命令执行 + 完整工具 → 10b。**默认按此。**
- **C · 10a 沙箱 = 工作区目录禁锢 + Agent SDK 权限模式锁到该目录**(无真容器隔离——10b);叠加有界循环 + 审 diff 后才"接受"当安全网。**默认按此。**
- **D · 有界循环 = 硬上限**(max_turns / 工具调用数 / 预算),超限→停+标 needs-review/失败。**默认按此**(成本 + 安全双控)。
- **E · 审 diff 的人在环 = 10a 就纳入**(coding_agent→human_review→批准/打回),复用 Phase 8。**我建议纳入**(无人值守改文件太险,这是安全运行的正确姿势,且零新机制)。你若想 10a 先做成"纯跑+只展示 diff、不卡审",告诉我。
- **F · 工作区 = 一个指定/临时工作区目录**(配置路径或临时目录),非 git clone/worktree(10b)。**默认按此。**
- **G · 离线纪律 = 接缝带可注入 fake、整族离线跑通、不设 key、不花钱**;真 Agent SDK 仅你本人真 E2E(计量)。**不可妥协**,沿用全程。

## 7. 红线

- **接缝是唯一的 Agent SDK 调用者**(镜像"llm.py 是唯一 SDK 调用者"),可换、可 mock;agent 循环只活在接缝里。
- **web 层仍只 import db + 纯数据**,no-SDK 守卫继续绿;coding 接缝是 worker 侧,纳入守卫排除集。
- **离线/建造不设 `ANTHROPIC_API_KEY`、不跑真 SDK/真发信、不合并**;真 E2E + 合并你本人来,且**真 E2E 走计量计费——心里有数**(有界循环兜底)。
- **agent 禁锢在工作区目录**,10a 无不受限 shell。
- **无回归判官**:既有 **323 测试逐字节绿**(digest/brief/HITL/no-SDK 守卫不动;编码 family 纯增量);分支 `phase10a`。

## 8. 流程(同 7/8:计划 review 一道,然后一口气建完)

> 这关是整套系统**跨边界最深**的一次,设计 review 尤其重(接缝形状、有界循环语义、工作区禁锢、family/node-kind 接法、审 diff 复用 Phase 8 的接点)。无回归判官只证旧行为不破、不证新接缝设计好。

1. 代码侦察(`llm.py` 接缝形状 + 注入纪律、Phase 8 的 family/harness/node-kind/engine、web-HITL 的 start/resume/RunDetail)+ 出 **U1 详细计划 + U2 概要**(含接缝签名、`CodingResult` 形状、编码 WorkflowDef、有界循环、工作区/权限做法)→ **停,等我 review 设计**,别直接建,并 flag 与本简报的任何出入。
2. 计划过 → **U1→U2 一口气建完,不每个 unit 停**:每单元以既有 323 测试原样绿当无回归判官 + 新机器离线测试(fake 接缝),自检通过即续。
3. 建完 U1 贴接缝签名 + `CodingResult` + 编码 WorkflowDef 落地形状给我扫一眼;不停、续 U2。
4. 全建完 + 既有测试全绿 → **报告 + 停**。
5. **你本人**真 E2E(真 Agent SDK,在一个真工作区给个小编码任务 → 跑出 diff → 网页审 → 批准/打回一次;留意计量用量)+ 合并 master。
6. 全程离线测试、**不设 key、不跑真 SDK、不合并**;分支 `phase10a`。
