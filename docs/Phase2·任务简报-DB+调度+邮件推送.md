# Phase 2 · 任务简报:DB + 调度 + 邮件推送

> **给执行的 build session:** 这是在 Phase 1 之上的第二片。完整设计见《Agent 系统 · 架构与建造蓝图》(单一真相来源)。Phase 1 的模块契约(fetch / agent / output)**继续有效,不要破坏**。
> **人类是 orchestrator**:范围之外的东西不要自作主张加;有歧义,问人。

---

## 1. 目标(一句话)

让新闻 workflow:① 每次运行**记录进 DB 并把简报存库**,② **按计划自动跑**,③ **把简报邮件发给我**。

## 2. 范围

**做(In):** DB(Run / Output / Schedule)+ 数据访问层;APScheduler 定时触发;SMTP 邮件推送;在 Phase 1 之上集成。

**不做(Out):** ❌ UI、认证(Phase 3)❌ 多 agent(Phase 5)❌ 多厂商(后期)❌ 云部署(Phase 3——本阶段先本地长跑进程)。

## 3. 已定决策(不要改)

- **调度:APScheduler**(进程内)。
- **推送:邮件(SMTP)**;凭据走环境变量;Gmail 之类用 **app 专用密码**。
- **DB:PostgreSQL**。
- **实体:Run、Output、Schedule**(见第 5 节)。

## 4. 和 Phase 1 怎么接

- Phase 1 的 **fetch / agent / output 三模块保持不变、继续用**。
- 新增一个 **runner**(编排一次完整运行):`fetch → agent → 存库(Output)+ 记 Run 状态 → 发邮件`。output 模块仍可写本地文件(保留),另外加"存库"。
- 调度器**只负责按时触发 runner**,不重复业务逻辑。

## 5. 共享契约(先锁死——这是并行的前提)

**DB schema(概念层,非建表语句):**

| 实体 | 关键字段 |
|---|---|
| **Run** | id、workflow(先固定 `"news"`)、状态(pending/running/success/failed)、started_at、finished_at、触发方式(scheduled/manual)、error(可空) |
| **Output** | id、关联 Run、类型(`digest`)、内容(markdown 或结构化 JSON)、created_at |
| **Schedule** | id、workflow、cron 表达式、是否启用、上次运行时间 |

**数据访问层接口(概念):** `create_run` / `update_run_status` / `save_output` / `list_due_schedules` 等。**其它单元只通过这层碰 DB**,不要把 SQL 散落各处。

## 6. 三个工作单元(可串可并)

- **单元 1 — 基础(先做、串行):** DB 连接 + schema(迁移)+ 数据访问层。**这是契约,先锁死它,单元 2/3 才能并行。**
- **单元 2 —(并行)调度器:** APScheduler 按 Schedule 触发 runner;runner 跑 Phase 1 流水线、把 Run/Output 写库。**支持手动触发一次**(便于测试)。
- **单元 3 —(并行)邮件推送:** 给定一份简报,通过 SMTP 发到我的邮箱;凭据走 env。**降级要求:邮件失败不能丢掉简报**——Run 照常记录、Output 照常存库,邮件失败单独记日志/Event。

## 7. 如何并行执行这份简报

1. 先一个 session 做**单元 1**,把 schema + 数据层落地并提交(锁死契约)。
2. 然后**单元 2、单元 3 可并行**:两个 session,各开一个 **git 分支 / worktree**,都对着单元 1 的数据层接口写,互不碰对方文件。
3. 最后你(orchestrator)整合,把 runner 串起来(`fetch→agent→存库→发邮件`),真实跑通。
4. **也可以全程串行**——并行只是可选;看你想不想练这次并行。

## 8. 技术约束

- Python ≥3.10;PostgreSQL;APScheduler;`smtplib`(或同类)发邮件。
- **Claude Agent SDK 认证:不要设 `ANTHROPIC_API_KEY`**;沿用 Phase 1 的 `tools=[]` 用法,并**钉死 SDK 版本 + 保留冒烟测试**(防升级回归——Phase 1 的教训)。
- 所有凭据(SMTP、DB)走环境变量,**不进代码 / 不进 Git**。
- **不破坏 Phase 1 的模块契约和测试。**
- 本地长跑进程即可(云部署是 Phase 3);"电脑得开着"这条限制此阶段成立,写进 README。

## 9. 工程规范

- Git 小步勤提交;并行用分支 / worktree。
- **测试(延续 Phase 1 的"离线、不调 API/网络"风格):** 数据访问层用测试库或事务回滚测;调度逻辑用 **mock 时间 / 手动触发** 测,不真等到点;邮件用 **mock SMTP** 测,不真发。
- 更新 README:怎么配 DB / SMTP、怎么跑、怎么手动触发一次。

## 10. 验收标准

1. **手动触发一次** → 新闻被抓取总结,Run 入库(success)、Output(简报)存库、**邮箱收到简报**。
2. 配一个 Schedule(如每天某点)→ 到点**自动**跑、自动入库、自动发邮件。
3. **邮件失败时**:Run 仍记录、Output 仍存库(优雅降级),失败有清晰日志。
4. 凭据不硬编码;Phase 1 模块契约与测试未被破坏。
5. 新增离线测试通过;README 更新;已提交进 Git。

## 11. 交付物

- DB schema / 迁移 + 数据访问层
- 调度器(可手动触发 + 定时)
- 邮件推送模块
- 串起来的 runner
- 离线测试 + 更新的 README + Git 历史

---

> 完成后人类 review 验收,再决定进 Phase 3(控制面 web app:认证 + 看状态/产出 + 排任务)。
