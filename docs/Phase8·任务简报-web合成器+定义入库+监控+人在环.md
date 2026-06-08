# Phase 8 任务简报 —— Web 合成器 + 定义入库 + 监控 + Web 人在环

> 北极星:把 Phase 7 的"定义即数据"从**代码里的数据**,变成**人能在网页上创作、编辑、运行的数据**。Phase 7 造了通用引擎能读任意 `WorkflowDef` 跑;Phase 8 让你不写 Python 就能造出新的 `WorkflowDef`。这是"定义即数据"对人兑现价值的一关,也是 Phase 9 meta-agent 的前置(meta-agent 起草的就是这里能存、能验、能跑的同一种数据)。

---

## 1. 目标

- **定义入库**:`WorkflowDef`/`AgentDef` 从 import 时的代码常量,变成 DB 里可读写的行;通用引擎按 id 从 DB 取定义跑。
- **Web 合成器**:网页上列/建/改/克隆/删工作流与 agent 定义——**从已注册的代码组件里挑积木 + 填 prompt/params + 连节点边**,存盘即成可运行工作流,能当场触发跑。(本期的"建"= 在 family 内克隆改;真·全新形状需写代码,见 §2.5 / §6-F。)
- **监控**:比 Phase 3 更细的运行视图(历史、状态、逐节点进度、产出)。
- **Web 人在环**:把 Phase 5 的 `interrupt()`/resume 闸搬上网页——在浏览器里批准/打回 review 节点。

## 2. 架构主线(Phase 7 推到这里的那一步):定义入库

Phase 7 决策 C 把定义放在代码/注册表、**不进 DB**,并明说"DB 等 Phase 8 合成器"。现在兑现:

- DB 新增 `workflow_defs`(+ `agent_defs`)表,存定义的 **JSON**(Phase 7 已把 `WorkflowDef`/`Node`/`Branch` 做成纯数据 + `"__end__"` 哨兵、不 import langgraph,所以能干净 JSON 化)。
- **解析顺序:DB 覆盖,否则代码默认。** 两个内建定义(`DIGEST_DEF`/`BRIEF_DEF`)**仍留在代码里**;引擎按 id 解析时先查 DB、没有再落到代码 `WORKFLOWS`。
  - **这条是无回归判官的命根子**:离线测试用空/未播种的 DB → 落到代码定义 → 既有 digest/brief 测试**原样绿**;DB 里的用户定义是**增量**;在合成器里改内建 = DB 里写一条覆盖行、影子掉代码默认。
- **代码组件注册表仍在代码里**,网页改不了——Phase 7 的 α 边界,也是 meta-agent 的边界:**合成器创作的是数据(引用按名注册的代码积木),不是代码。** 注册表含 `prompt_builders / parsers / agents / sources / renderers / node_handlers / predicates / composers`,**外加每个工作流的状态 schema(`_State` TypedDict)**——状态 schema 也是代码、不在 def 里,这条决定"新建工作流"能做到哪(见 §2.5)。

## 2.5 连带影响:通用运行路 · state_ref · 内建只读(Phase 7 作者代码侦察,计划 review 钉死)

这三件连在一起,决定"存盘即可跑""新建到底能做到哪",以及整套怎么不破既有测试。

- **通用运行路(U2 的真硬骨头)**:`engine.build_graph(wf, state_schema, *, node_handlers, predicates, composers)` 吃"def + 状态 schema + 代码组件",**不吃 id**。"按 id 跑" = 一层 **resolver**(按 id 给 def:DB 覆盖否则代码)+ 一条**通用运行路**(source items → initial_state → 注入 agent 到 `config["configurable"]` → 跑 → 投递)。内建 digest/brief 复用 `build_digest`/`build_brief`(只把 def 改从 resolver 取,最稳无回归);**新工作流没有 `build_X`,"存盘即可跑"靠这条通用路**。runner 维持内建 dispatch(被 test_runner/test_handoff 钉死)+ 并存通用路(沿用 D3,别替换内建)。
- **状态 schema 是代码、不在 def 里 → def 加 `state_ref`**:每个 handler 读写特定 state 键;digest 与 brief 的 `_State` 是两个**不兼容**的 TypedDict。把 digest 的 summarize 和 brief 的 filter 混进一个新 def **跑不起来**,且结构校验拦不住。解法:状态 schema 也作按名引用的代码组件,def 加 `state_ref`(digest→digest_state、brief→brief_state)。**于是"新建工作流"的真实范围 = 在某 family(digest/brief)内克隆 + 改 prompt/params/拓扑/可兼容积木;真·全新形状(新 state schema/新 handler)仍需写代码**——与 meta-agent 的 palette 限制一致。
- **内建只读 = 承重墙(见决策 E)**:`orchestrator._BUILDER` 在 import 期就用代码 `DIGEST_DEF` 建好、`build_digest` 用 `_APP`、`agent.py` 直接读代码 `AGENT_DEFS`。内建只读 ⇒ DB 覆盖只作用于 runner 的 by-id 路(新/克隆工作流),内建全留代码路 ⇒ **import 期零 DB、既有 test_human_in_the_loop / test_runner / test_handoff 不破**。允许覆盖内建会把 DB 拉进 import 期、每次重解析——几乎是唯一免坑选法。
- **校验是结构性、非语义**:`validate_workflow_def` 查"引用存在 + 拓扑合法",但结构合法的 def **仍可能运行时崩**(builder 吐 list、handler 按单项取;parser/builder 不配套;state 键对不上)。靠"克隆 + family 内兼容积木"从源头挡,别让合成器/meta-agent 以为"过校验 = 能跑"。

---

## 3. 范围(在做什么 / 不做什么)

**做**:定义入库 + 解析顺序;合成器 CRUD(结构化表单,见决策 §6-B);存盘期校验(见 §6-C);从合成器触发跑;细化运行监控;web 批准/打回。

**不做(本期)**:可视化拖拽画布(决策 §6-B 推迟);多人/协作/分享;完全数据化 runner 的 intake/delivery(Phase 7 D3 推迟项,继续推迟);上云。

## 4. 单元拆分(计划过关后一口气建,见 §7)

- **U1 · 定义入库 + 解析顺序**:`workflow_defs`/`agent_defs` 表 + DAO(数据面);`WorkflowDef ↔ JSON` (反)序列化;引擎按 id 解析改"DB 覆盖否则代码默认";内建定义可选播种进 DB(也可只靠代码回落)。**判官**:既有 digest/brief 全测试原样绿(空 DB 走代码路);新增 JSON 往返、DB 覆盖/回落、解析顺序单测。纯离线。
- **U2 · Web 合成器(CRUD over defs)**:控制面新增合成器页面 + API——列/建/改/克隆/删工作流与 agent 定义,结构化表单从**注册组件清单(§6-D)**挑积木、填 prompt/params、连边;**存盘期校验**;存盘即可在列表里"立即运行"。**判官**:web 层 no-SDK/no-langgraph 守卫继续绿;新增合成器 API 的鉴权/CSRF、校验拒坏定义、CRUD 往返测试。这是中心件、Phase 7 的兑现。
  - **(保险)** 建完贴一下合成器的数据契约(定义 JSON 形状 + 注册组件清单形状)给我扫一眼,不停、续 U3。
- **U3 · 监控 + Web 人在环**:细化运行视图(历史/状态/逐节点/产出);把 `start_review_run`/`resume_review_run` 闸接上网页(浏览器里批准/打回)。"工作区"概念在此轻量落地(把定义/运行/产出归拢)。**判官**:既有 `test_human_in_the_loop` 行为不破;新增 web review 流程测试。真 SDK/真发信 E2E + 合并 = **你本人**。

## 5. 模块/数据(worker 侧新模块继续纳入 web no-SDK 守卫)

- DB:`workflow_defs`/`agent_defs` 表 + DAO(只数据面)。
- 序列化:`WorkflowDef`/`AgentDef` ↔ JSON(纯数据,免 import langgraph)。
- 校验:`validate_workflow_def(def, manifest)` —— 引用的组件名都在注册表、拓扑合法(有 entry、边目标存在或为 `"__end__"`、无悬挂、`__end__` 可达)。
- resolver + 通用运行路:**resolver 按 id 取 def(DB 覆盖否则代码 `WORKFLOWS`)**;内建走 `build_digest`/`build_brief`(def 从 resolver 取),新工作流走通用路(def + `state_ref`→状态 schema + 按名注入 agent → `engine.build_graph` → 跑);runner 内建 dispatch + 并存通用路(D3)。
- 状态 schema:作为按名引用的代码组件,def 加 `state_ref`(digest→digest_state、brief→brief_state);序列化/校验都带上它。
- 清单(§6-D):worker `build_manifest()` 内省 `components` → **写进 DB**,web 从 DB 读(防手维护漂移、不破守卫)。
- 监控数据面:**逐节点进度 = 结构化 `Event` 表(蓝图 §4 后补项)+ worker 把 LangGraph 节点事件流式写 DB**;顺手把 Phase 5 欠的"review verdict 落 Run"一起做。
- Web:合成器页面 + API(控制面,只 import db);**注册组件清单**以纯数据暴露给 web(见 §6-D)。

---

## 6. 决策点(请确认或否决 / 给方向)

- **A · 解析顺序 = DB 覆盖否则代码默认**,内建定义留代码、空 DB 走代码路(保无回归判官)。**默认按此。**
- **B · 合成器形态 = 结构化表单**(挑积木 + 填字段 + 连边,无裸 JSON、无可视化画布)。比"裸 JSON 编辑器"好用、比"拖拽画布"省一个数量级且不掉进 UX 兔子洞;可视化画布**推迟**。**默认按此**;你若想要画布(大很多)或只要裸 JSON(更小)告诉我。
- **C · 存盘期校验是安全红线**:web/合成器存的坏定义(引用不存在的组件、拓扑断裂)必须**存盘时就拒**,绝不能等到 worker 半夜定时跑才崩。校验既在存盘 API、也在引擎加载处兜一道。**默认按此。**
- **D · 注册组件清单给 web 的方式**:校验和表单下拉都要知道"有哪些可用积木(node-kind/handler/parser/source/renderer/agent id)"。但 web 层不能 import 引擎/components(会拉进 SDK/langgraph、破守卫)。→ 暴露一份**纯数据的清单(名字/类型)**给 web。**来源防漂移**:worker `build_manifest()` 从 `components` 内省 → 写进 DB,web 从 DB 读(别手维护清单、别让 web import components/engine)。worker 侧做完整(含语义)校验。**默认按此**(Phase 7 那类边界坑)。
- **E · 改内建工作流**:允许在合成器里改 digest/brief(写 DB 覆盖行影子代码默认),还是内建只读、只能克隆出新的去改?**我建议"可克隆、内建本身只读"**——不只保判官基线:**内建只读才让 `_BUILDER`/`build_digest`/`agent.py` 全留代码路、import 期零 DB、既有测试不破(见 §2.5)**;允许覆盖内建会把 DB 拉进 import 期。创作从克隆起步。**强烈建议拍"内建只读、克隆才改"。** 请拍板。
- **F · "新建工作流"的范围(Phase 7 作者新增)**:本期 = 在 family(digest/brief)内**克隆 + 改 prompt/params/拓扑 + 换可兼容积木**(def 加 `state_ref` 指向注册的状态 schema);**真·全新形状(新 state schema / 新 handler)需写代码、本期不做**。理由见 §2.5(状态 schema 是代码、handler 有 state 键契约,任意混搭跑不起来且校验拦不住)。**建议按此**;若要"任意从零拼"那是大得多的工程(要把 state 也数据化),否决告诉我。

## 7. 流程(同 Phase 7:计划 review 一道,然后一口气建完)

> 关口仍是**计划 review(设计先过纸面)**——这关同样设计重(DB schema、解析顺序、校验、清单暴露、合成器数据契约),无回归判官只证行为、不证设计好,而这套是 Phase 9 的地基。

1. 代码侦察(Phase 7 落地的 `workflows.py`/`engine.py`/`components.py`/两个 orchestrator 现状、web 层结构、DAO 现状)+ 出 **U1 详细计划 + U2/U3 概要**(含 §5 的具体 schema/JSON 契约/校验规则)→ **停,等我 review 设计**(别直接建),并 flag 与本简报的任何出入。
2. 计划过 → **U1→U2→U3 一口气建完,不每个 unit 停**:每单元以既有 digest/brief/human-in-the-loop 测试**原样绿**当无回归判官 + 新机器离线测试,自检通过即续。
3. 建完 U2 顺手贴一下落地的**定义 JSON 形状 + 注册组件清单形状**给我扫一眼;不停、续 U3。
4. 三单元全建完 + 既有测试全绿 → **报告 + 停**。
5. **你本人** review + 跑真 E2E(web 合成器建一个新工作流 → 存盘 → 立即运行,经通用引擎、中文、邮件到;改一个内建/克隆;web 批准/打回一次 review)+ 合并 master。
6. 全程离线测试、**不设 `ANTHROPIC_API_KEY`、不跑真 SDK、不合并**(E2E + 合并你本人来);分支 `phase8`。
