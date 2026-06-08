# Phase 7 · 任务简报 — 工作流与代理"数据化" + 通用编排器
## Switchboard 的概念心脏(定义即数据)

> 分支 `phase7`(从**已合并 Phase 6** 的最新 master 切)。最重的一个 phase,**增量 3 unit**,每单元都以"既有测试原样绿"当无回归判官。

---

### 1. Context 与目标

到 Phase 6 为止,你有**两个手写工作流**:digest(`build_digest`:总结→审查→有界重做)和 brief(`build_brief`:过滤→逐条 摘要+多视角→组装)。两个都是 LangGraph 图、dict-state、agent 经 `llm.py` 注入,各有自己的契约/节点/管线,靠 runner 按 `workflow` 字段 dispatch。

**目标**:把"agent 定义""工作流定义"从**写死的代码**变成**结构化数据**;一个**通用编排器**读一份工作流定义 → 动态编译成 LangGraph 图 → 执行。兑现蓝图第 3 节"定义即一等数据"——这是 meta-agent(Phase 9)能"读定义、写定义"的前提,也是合成器(Phase 8)能编辑的对象。

**为什么现在能做**:有了 digest + brief **两个真实形状**(线性+有界循环 / 扇出),通用 schema 才有真实差异可归纳,不是凭空抽象。

---

### 2. 范围 / 非目标

- **范围**:agents-as-data(定义 + 组件注册表)、workflows-as-data(定义 schema)、**通用编排器**(定义 → StateGraph 编译器)、**dogfood**(把 digest 和 brief 都重表达成定义,替掉各自手写的图)。
- **定义先以结构化数据/配置形态存在**(Python dict / YAML 皆可),**不进 DB**。
- **非目标(后续 phase)**:定义存 DB(Phase 8 合成器需要写时再上)、合成器 UI(8)、meta-agent(9)。

> 关键 YAGNI 取舍:**先把"通用编排器读定义执行"跑通,定义放配置;DB 持久化等 Phase 8 合成器真要写时再加。** 别现在就建 DB 写入路径。

---

### 3. 设计约束 / 红线

- **保接缝**:通用编排器的节点仍**只经 `llm.py` 调模型**(模型无关、`tools=[]`);LangGraph 编排、不直连 SDK。
- **行为不变(本 phase 的命根子)**:dogfood 后,**既有 digest / brief 测试必须原样绿** —— 这就是"通用编排器跑出来的图与手写图行为一致"的证明,同 Phase 5 迁 LangGraph 时"字节级一致"那招。
- **数据 vs 代码的线**(决策 A):**prompt / model / 参数 = 数据;parser / 契约 / 渲染器 / 来源采集 = 代码**,由定义**按名引用**(组件注册表)。理由:解析/渲染是逻辑不是数据,硬塞进数据反而脆。
- **schema 表达力够用即可、不做通用工作流语言**(决策 B):只覆盖两个真实工作流需要的控制原语(见 §5),别造图灵完备的 DSL。
- **给人在环留位**:schema 设计要**允许**将来有 `human_review`/interrupt 节点(Phase 5 Unit 3 的原语),但本 phase **不必 dogfood 它**(当前两个工作流都不用)。别把 schema 设计到塞不进它。
- 离线确定性测试、不设 `ANTHROPIC_API_KEY`;web 层 no-SDK/no-langgraph 守卫继续绿(通用编排器是 worker 侧)。

---

### 4. 数据模型(草案,session 据真实代码细化)

```
AgentDef:    { id, prompt_template, model, params:{...}, parser_ref }   # parser_ref 指向代码注册表里的解析器
WorkflowDef: { id, source_ref?, nodes:[Node], output_ref }             # source_ref/output_ref 指向代码注册表(采集/渲染+投递)
Node:        { id, kind, agent_ref?, ... }                             # kind ∈ §5 控制原语
```

- `*_ref` 都是**按名引用代码组件**(parser / source-gatherer / renderer+deliverer),组件本身留代码、注册进一个 registry。
- `params` 例:brief 的 `stances=[商业,政策,技术]`、`keep_cap=8`;digest 的 `max_redos=2`。**这些本是写死的常量,正好变成定义里的数据。**

---

### 5. 控制原语(schema 要表达的,正好覆盖两个工作流)

| 原语 | 干什么 | 谁需要 |
|---|---|---|
| `step` | 跑一个 agent(按 `agent_ref`)over state | 两者 |
| `conditional` | 按 state 上的判定分支 | digest(审查 pass/fail) |
| `loop`(有界) | 带上限回跳(用尽则走 accept-last) | digest(重做) |
| `fan_out` / `map` | 对一个 list 逐项跑一段子序列 | brief(逐条 摘要+多视角) |
| `gather` / `compose` | 汇总结果成产出契约 | brief(组装 Brief) |

通用编排器把这些原语**编译成 LangGraph**(节点 / 条件边 / 有界循环 / Send-map)。**不需要支持任意图**——够这两个用、且能推广到同类即可。

---

### 6. 单元拆解 + 验收门(增量,逐工作流归纳)

> 思路:**一次归纳一个工作流,每步用它既有的测试当无回归判官**;控制表达力**从 digest 的简单控制起、到 brief 的扇出**逐步加。这是把最难的 phase 做稳的方式。

**Unit 1 — Agents-as-data + 组件注册表**
- 定义 `AgentDef`(prompt/model/params + `parser_ref`)+ 一个**组件注册表**(parser / source / renderer 按名注册)。
- 把现有 5 个 agent(summarize、verify、filter、summarize_item、perspective)的 **prompt/model/params 抽成 AgentDef**;parser 留代码、注册按名引用。
- 现有 `build_digest`/`build_brief` 改为**从 AgentDef 装配 agent 调用**(prompt/model 来自数据,parser 来自注册表)——**图结构先不动**。
- **门**:既有 digest + brief 测试**原样绿**(agent 改从数据装配、行为不变);新增 AgentDef 加载 + 注册表测试。

**Unit 2 — WorkflowDef + 通用编排器(线性/条件/有界循环)+ dogfood digest**
- 定义 `WorkflowDef` schema(§4)+ 控制原语 `step`/`conditional`/`loop`(§5 前三个)。
- **通用编排器**:读 WorkflowDef → 编译成 LangGraph StateGraph → 执行(节点经 `llm.py` 调 AgentDef 装配的 agent)。
- **把 digest 重表达成一份 WorkflowDef**;`build_digest` 改为"用通用编排器跑 digest 定义";手写图删除。
- **门**:**既有 digest 测试原样绿**(通用编排器跑出的行为 = 手写图,无回归证明);新增编排器 + 控制原语测试。

**Unit 3 — 扩 `fan_out`/`gather` + dogfood brief + 真 E2E**
- 给 schema + 编排器加 `fan_out`/`gather`(§5 后两个)。
- **把 brief 重表达成 WorkflowDef**;`build_brief` 改为用通用编排器跑;手写图删除。
- **门**:**既有 brief 测试原样绿**;新增 fan_out 测试;**真 SDK + 真抓 + 真发信 E2E 一次**(`run-once --workflow brief` 与 `--workflow digest` 都经通用编排器跑通、产出与现在一致、中文、邮件到)。

> dispatch 不变:runner 仍按 `run.workflow` 找定义(`"news"`→digest 定义、`"brief"`→brief 定义),只是现在交给通用编排器跑,而非调写死的 `build_*`。定义此期放配置/注册表;按 id 查。

---

### 7. 测试(全离线,不设 key)

- **无回归判官**:既有 digest(Unit 1、2)与 brief(Unit 1、3)测试**全程原样绿**——这是通用化行为一致的核心证明。
- 新增:AgentDef 加载 + 组件注册表;WorkflowDef 校验;通用编排器对每个控制原语(step/conditional/loop/fan_out/gather)的单测;空输入/上限/有界循环边界。
- 守卫:通用编排器及新模块纳入 web 层 no-SDK/no-langgraph 集合。

---

### 8. 流程(一个 session 内:计划 review 一道,然后一口气建完)

> 本 phase 的关口是**计划 review(设计先过纸面)**,不是每个 unit 停。理由:无回归判官只证"行为一致"、不证"抽象设计好",而这套 schema 是 Phase 8 合成器 / Phase 9 meta-agent 的地基,设歪了返工最贵。

1. 先做**代码侦察** + 出 **Unit 1 详细计划 + Unit 2/3 概要**(含 §4/§5 的具体 schema)→ **停,等我 review 设计**(别直接建)。
2. 计划过 → **Unit 1 → 2 → 3 一口气建完,不每个 unit 停**:每个 unit 以其对应的既有测试(digest / brief)**原样绿**当无回归判官 + 新机器离线测试,自检通过即续下一个。
3. (可选保险)建完 Unit 2 顺手在进度里**贴一下落地的 `WorkflowDef` schema 形状**,让我早点扫一眼抽象;不打断、继续 Unit 3。
4. 三个 unit 全建完 + 既有测试全绿 → **报告 + 停**。
5. **你本人** review + 跑 Unit 3 真 E2E(digest/brief 都经通用编排器、产出一致、中文、邮件到)+ 合并 master。
6. 全程离线测试、**不设 `ANTHROPIC_API_KEY`、不跑真 SDK、不合并**(E2E + 合并你本人来);分支 `phase7`。

---

### 9. 决策点(请确认或否决 / 给方向)

- **A · 数据 vs 代码的线**:prompt/model/params = 数据;parser/contract/renderer/source = 代码按名引用(组件注册表)。**默认按此**;你若想把 parser 也数据化(更"纯"但更脆),否决告诉我。
- **B · schema 表达力**:只覆盖 §5 五个原语、不做通用 DSL。**默认按此。**
- **C · 定义存放**:本期放**配置/注册表(code/YAML),不进 DB**;DB 等 Phase 8 合成器。**默认按此。**
- **D · 增量顺序**:Unit 1 抽 agent → Unit 2 dogfood digest(简单控制)→ Unit 3 加 fan_out + dogfood brief。**默认按此**(每步既有测试当判官)。
- **E · 人在环节点**:schema **留位**但本期**不 dogfood**。**默认按此。**

> session 应先读真实代码(两个工作流的图/契约/parser/runner dispatch),据此细化 §4/§5 的具体 schema,并 flag 与本简报的任何出入(像 Phase 6 逮到 `workflow` 列那样)。
