# Phase 3 · Unit 1 任务简报:后端 API + 认证

> **给执行的 build session:** 这是 Phase 3 的第一块(地基)。完整设计见《Agent 系统 · 架构与建造蓝图》(单一真相来源,在 `docs/`)。Phase 1 + Phase 2 已完成并合并进 `master`(`db` 数据层、`runner`、`scheduler`、`mailer`)——**不要破坏它们**。
> **人类是 orchestrator**:范围外的东西不要自作主张加;有歧义,问人。

---

## 1. 目标(一句话)

在现有 `db` 数据层之上,用 **FastAPI 提供一个带登录的 REST API 控制面**:登录后能查看运行/状态/产出、管理排程、手动触发一次运行。

## 2. 关键架构约束(别违反)

**web API 是独立进程,和 worker 共享同一个 DB。它读状态/产出、写排程定义,但绝不自己同步跑 agent**(跑 agent 仍是 Phase 2 的 worker/scheduler 进程)。所以"手动触发"必须**交给 worker 执行**,不能在 web 请求里同步跑 `run_once`(会阻塞请求、把 SDK 塞进 web 进程)。怎么交接由你提方案(见 §4)。

## 3. 范围

**做(In):** FastAPI app;认证(单用户);REST 端点(见 §4,全部走现有 `db` dao,不在 API 里写裸 SQL);users 表迁移 + 建用户的 CLI;离线测试(TestClient);OpenAPI 即前后端契约。

**不做(Out):** ❌ React 前端(Unit 2)❌ 上云(后续独立一步)❌ 多用户/角色 ❌ meta-agent/多 agent。只 import/复用 Phase 1/2 模块,不改它们。

## 4. API 端点(契约 · 概念)

- **认证**:`POST /auth/login`(用户名+密码 → 下发会话 cookie)、`POST /auth/logout`、`GET /auth/me`(查当前登录态)。
- **运行/产出(只读)**:`GET /runs`(近期列表)、`GET /runs/{id}`、`GET /runs/{id}/output`(看简报)。
- **排程(增删改查)**:`GET /schedules`、`POST /schedules`、`PATCH /schedules/{id}`(启停/改)、`DELETE /schedules/{id}`。
- **手动触发**:`POST /runs`(或 `/trigger`)——**交给 worker 执行**(见 §2)。**请在计划里提出交接方案**(例如:写一条 pending Run,让 worker 心跳顺带捞起执行)。

除认证外,所有端点都要求登录。

## 5. 决策(已定)

- **单用户**:不做用户管理,就一个登录账号。
- **users 表**:按蓝图 §4 加(id、username、password_hash);新增一个 Alembic 迁移;**密码用 bcrypt/passlib 哈希**;提供一个 **CLI 命令建用户/设密码**(把明文挡在代码/Git 外)。
- **认证机制**:登录成功 → **httpOnly + SameSite + Secure(部署时)会话 cookie**;**用成熟方案,绝不自己手搓加密**。cookie 方案注意 **CSRF**(SameSite 能挡大半;细节你在计划里说清楚)。
- **本地优先**:本阶段只在 localhost 跑通;上云是后续独立一步。

## 6. 技术约束

- FastAPI;Python ≥3.10;**所有 DB 操作走现有 `db` dao**,不在 API 层散落 SQL。
- 会话密钥等 secret 走环境变量,不进代码/Git。
- **不破坏 Phase 1/2 的模块与现有 61 个测试**;worker/scheduler 仍作为独立进程运行。
- 离线测试:用 FastAPI TestClient + 测试库,不连真网/不调 SDK。

## 7. 工程规范

- 在 `phase3` 分支上小步提交。
- 测试:认证(登录成功/失败、未登录被拒、受保护端点需登录)、各端点(读 + 排程 CRUD)、手动触发的交接逻辑——全离线。
- 更新 README:怎么跑 API、怎么建用户、需要哪些 env。

## 8. 验收标准

1. CLI 能建用户;经 `/auth/login` 登录拿到会话 cookie;**错密码被拒、未登录访问受保护端点被拒**。
2. `GET /runs`、`/runs/{id}/output` 返回 DB 里的真实数据;排程 CRUD 生效并反映到 DB(**正在跑的 scheduler 能动态捞到**,无需重启)。
3. 手动触发**创建一条由 worker 执行的运行**(交接),不在 web 进程里同步跑 agent。
4. secret 走 env;Phase 1/2 未破坏;旧测试仍绿 + 新 API 测试通过。
5. `/docs`(OpenAPI)可用;README 更新。

## 9. 交付物

- FastAPI app + 认证 + 基于 dao 的端点
- users 表迁移 + 建用户 CLI
- 手动触发的 worker 交接(按你提的方案)
- 离线 API 测试
- README 更新

---

> 这是 Unit 2(前端)要依赖的契约。**先给我实现计划**——重点说清:① 认证的具体方案(cookie/会话怎么管、CSRF 怎么处理);② 手动触发怎么交接给 worker。我确认后再写;做完暂停等我 review。
