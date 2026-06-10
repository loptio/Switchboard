# Phase 9 · 任务简报 — meta-agent（带护栏）

> 状态：本简报由接手的 build session 起草并当夜建造（用户授权"直接动、不用审"）。
> 蓝图 §3/§7 的兑现：**meta-agent 只是同一份定义数据的另一个写入者**，
> 它产出"可审的数据"，永远不产代码；人审门是它落盘前的必经关卡。

## 1. 目标

一个 **meta 工作流家族**：输入一句自然语言请求（"给我一个只看安全/伦理/市场三个视角的简报变体"），
meta-agent 起草一份 **WorkflowDef 提案（+ 可选的新 AgentDef）**，经过：

1. **确定性验证节点**（defs_validate 两护栏 + meta 专属检查，非 LLM）；
2. 验证失败 → 错误喂回重画（**有界 redo**，max_redos=2）；
3. 验证通过 → **人审门**（interrupt + checkpointer，web/CLI approve/redo+feedback）；
4. **approve 才落盘**（经现有 defs CRUD 写入 DB），reject/give-up 一字不写。

落盘后的 def 立即可被 `POST /runs {"workflow": <new_id>}` 运行——meta-agent 是合成器的对等写入者。

## 2. 范围（做 / 不做）

**做（U1 · 动态 agent 命名空间）**：保存时的 workflow 验证把 `agents` 命名空间从
"manifest 5 个内置"扩为 "内置 ∪ DB AgentDef ids"。运行时本就支持
（`defs_resolve.resolve_agent_def` DB-only id 可解析；`runner._make_agent_fn`
凭 (builder,parser) 对找基础可调用），只有保存验证在拦。这同时惠及人用的合成器。

**做（U2 · meta 家族）**：meta_agent.py（起草 agent：llm.complete 经典模式 + 严格 parser）
+ meta_orchestrator.py（dict-state 图：draft → validate → human_review，本地注册表，
照 coding 家族的 worker-side island 模式）+ workflows.META_DEF（id="meta"，代码内置，
不进 manifest）+ runner 接线（straight 路径**拒绝** meta、review 路径完整、finalize 落盘）
+ CLI（run-once --workflow meta --task "..."，自动升 review）+ web（ReviewPanel 提案视图、
RunsDashboard meta 触发）。

**不做**：meta 起草 coding 工作流（coding 不在 manifest，验证自动拒——保持）；
meta 修改/删除既有 def（只允许新 id）；per-agent model 路由（字段存在但运行时未接线，
提案强制 model=null）；meta 提示词的 DB 化（draft agent 是代码接缝，不是 AgentDef——
照 coding_fn 先例）；排程 meta 运行（手动触发 + 必审，天然不可无人化）。

## 3. 护栏（蓝图 §7 的逐条兑现）

1. **调色板硬边界**：提案只能重组 manifest 注册名（验证拒绝一切未注册 ref；
   meta_*/coding_* 处理器不在 manifest → meta 不能起草 meta/coding 工作流，不可自我修改）。
2. **人审必经**：runner 的 straight 路径对 output_ref=="meta" 直接 mark_failed
   （"meta workflow requires review"）；CLI/web 触发 meta 时自动强制 review=True。
   approve 之前 **零持久化**（提案只活在 checkpoint + type="review" Output 里）。
3. **只新不改**：提案的 workflow id 与 agent ids 必须是新 id——与 WORKFLOWS 内置、
   AGENT_DEFS 内置、DB 两表既有行都不冲突（id 规则 `^[a-z][a-z0-9_-]{1,39}$`）。
   recon 发现 **DAO 层无内置只读保护**（只有 API 路由有）——worker 落盘路径自带该检查。
4. **运行时可行性**：新 AgentDef 的 (prompt_builder_ref, parser_ref) 必须命中内置组合
   （`_AGENT_BASE_BY_REFS` 的硬约束，否则运行时 ValueError）；处理器前缀须与家族一致
   （digest_*/brief_*——防跨家族状态键崩溃）；model 强制 null（未接线，防静默无效）。
5. **落盘前再验**（TOCTOU）：finalize 在 approve 后、写库前重跑全部检查
   （DB 在挂起期间可能已被人写入同名 def）。
6. **审计**：request/提案/验证报告/尝试次数 全程入 type="review" 与 type="meta" Output；
   Run 状态机就是审计时间线。结构化 Event 表仍按 BACKLOG 触发条件延后。

## 4. 单元与判官

- **U1 动态命名空间**：api/routers/workflows.py 的 `_validate_or_400` 改用
  "manifest ∪ DB agent ids"。判官：新增 API 测试（引用 DB agent 的 def 可存；
  未知 agent 仍 400）+ 既有全绿。
- **U2 meta 家族**：上述模块 + runner/CLI/web 接线。判官：新增离线测试
  （parser/prompt builder/图三态：通过-重画-放弃/approve 落盘/redo 反馈/拒绝不落盘/
  冲突拦截/straight 拒绝/no-SDK 守卫扩集）+ **既有 401 pytest、39 vitest 逐字节绿**。
- **真 E2E（tools=[]，订阅认证）**：meta 是裸模型调用家族（与 digest/brief 同风险级），
  本 session 获夜间授权执行一次冒烟 E2E；coding（shell）仍属用户 hands-on。

## 5. 决策点（已定）

- **A · request 入口 = 复用 runs.coding_task 列**（0006 迁移的既有管道，CLI --task / web 字段
  / API body 全通；不再开迁移。列名是历史遗留，语义即"per-run task"——文档备注）。
- **B · meta 不进 manifest**（照 coding）：不可被合成器/它自己起草；家族即代码。
- **C · 落盘者 = runner._finalize_meta**（不在 human_review 处理器里写库——interrupt 前
  必须无副作用，resume 会从头重放节点；recon 确认的 LangGraph 语义）。
- **D · 提案契约** = `{"workflow_def": {...}, "agent_defs": [...], "explanation": str}`，
  解析容忍围栏/散文（沿用 agent.py `_extract_json_object` 惯例），缺键/坏形即
  AgentContractError → 计入有界 redo。
- **E · give_up**（max_redos 用尽仍不过验证）→ run failed，最后一版提案 + 错误清单
  存 Output 供事后检查（与 digest accept_last 的"透明降级"同精神）。

## 6. 红线（不变项）

- `ANTHROPIC_API_KEY` 绝不设置；订阅认证。
- llm.py 仍是唯一 tools=[] SDK 调用者（meta 起草走它）；coding_agent.py 仍是唯一 agent-loop 调用者。
- web 层零 SDK/langgraph：meta_agent/meta_orchestrator 进 test_api_no_sdk 禁入集；
  web 只碰 db + 纯数据模块。
- 既有测试逐字节绿是无回归判官；控制/执行面解耦（web 写意图、worker 执行）不动。

## 7. 与北极星的关系

Phase 7 划下 data/code 线，Phase 8 让人经 web 写 defs，Phase 9 让 **agent 在人审门后写 defs**
——同一张表、同一套验证、同一个审门。三个写入者（代码/人/meta-agent）此后完全对称。
膨胀调色板（新 source/renderer/handler 家族）从此是"给 meta-agent 更大词汇表"的独立工作，
与 meta 机制正交。
