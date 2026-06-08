# Phase 10b-1 任务简报 —— 可指挥的编码 agent + git 感知工作区

> 北极星(Phase 10):在 Switchboard 里跑会用工具、对世界动手、能互相对话的 coding agent。10a 跨过了边界(单 agent、文件读写、有界、禁锢、审 diff),但还是个"证明":任务写死在 config、工作区是临时目录、产出是快照 diff。**10b-1 把它变成你能真指挥的东西**:每-run 自定任务、对准真 git 仓库、看真 git diff、审过再留(打回则 git 还原重跑)。
>
> **范围决策(关键,见 §2 / 决策 D)**:10b-1 **仍只文件读写、不开 shell**。真 shell 必然要配真沙箱,而沙箱是整条编码路上最硬、最险的一块,值得**单独一关(10b-2)**专心做,不塞进这里。10b-1 = 高价值、低风险、快;10b-2 = 那块硬骨头。

---

## 1. 目标
- **每-run 任务输入**:网页/CLI 给这一次 run 一个具体任务(不再是 config 全局任务)。
- **每-run 工作区**:对准一个真 git 仓库(不再是临时目录)。
- **git 感知 diff**:工作区是 git 仓库 → 用 `git diff`/`git status`(真实、干净);非 git → 回落 10a 的快照 + difflib。
- **审 diff 闭环升级**:批准 = 留下改动(你自己 commit);打回 = `git checkout`/`stash` 还原 + 带反馈有界重跑(干净重来)。
- 仍 **只文件读写(Read/Write/Edit),不开 Bash**(同 10a)。

## 2. 范围(做 / 不做)
**做(10b-1)**:Run 加 `coding_task`/`coding_workspace` 列 + 迁移;网页触发控件加任务输入(+工作区);runner 从 Run 读 task/workspace(**Config 仍作回落,保 10a 行为**);CLI `run-once --workflow coding --task ... --workspace ...`;git 感知 diff + 还原式打回。

**不做**:
- **真 shell/命令执行 + 沙箱**(→ **10b-2**,单独一关:给 agent 跑测试/构建的能力,也是最大攻击面;shell ⟹ 真沙箱(容器隔离),那块自己就是一关)。
- git 分支/commit/PR 自动化(→ 10b-2/3;10b-1 批准后你自己 commit)。
- 会话生命周期 UI(起/接/停/列、实时流)(→ 10b-3)。
- 多 agent 互相对话(→ 10c)。

## 3. 单元
- **U1 · 每-run 任务/工作区贯穿全栈**:Run + `coding_task`/`coding_workspace`(迁移 0006);runner coding 路径从 Run 取(无则回落 Config);CLI 加 `--task`/`--workspace`;网页触发控件加任务 textarea(+工作区)。**判官**:既有 364 测试原样绿(Config 回落保 10a 行为)+ 新测试(per-run 任务流穿到 seam、回落正确)。
- **U2 · git 感知工作区**:工作区是 git → `git diff`/`git status` 出 diff + changed_files;非 git → 回落快照 + difflib;打回 → `git checkout`/`stash` 还原后有界重跑。**判官**:既有 coding 测试原样绿(非 git 路径 = 10a)+ 新 git 测试(临时 git 仓库,离线、无 SDK/无钱:fake seam 改文件 → git diff 出真 diff;打回还原)。
  - **(保险)** 建完 U1 贴 Run schema 改动 + per-run 任务怎么流到 seam 的形状给我扫一眼;不停、续 U2。

## 4. 决策点
- **A · 每-run 任务/工作区 = Run 加两列(迁移)** + 网页字段 + CLI flag 喂;Config 仍作回落(保 10a / back-compat)。默认按此。
- **B · git 感知**:工作区是 git → git diff/status;非 git → 快照 + difflib 回落。默认按此。
- **C · 打回语义**:git 仓库用 `git checkout`/`stash` 还原后再有界重跑;批准 = 留改动、**不自动 commit**(commit/PR 是 10b-2/3)。默认按此。
- **D · 仍不开 shell(关键范围决策)**:10b-1 文件读写 only;真 shell + 沙箱 = 10b-2 独立一关。**默认按此**;你若坚持这一关就把 shell+沙箱做了,告诉我(那会大很多、险很多)。
- **E · 工作区安全**:agent 现在改的是**真仓库**(非临时目录)——10a 的 `confine` 仍生效(锁在仓库根,含你刚修的软链处理);审 diff 把关接受;打回 git 还原。**无需新沙箱(因为无 shell)**。提醒:对准一个你愿意让它改的仓库,审过再留。默认按此。

## 5. 红线
- **仍无 Bash/shell**(文件读写 only)——10b-1 不新增执行攻击面;安全 = confine + 审 diff + git 还原(都已验)。
- 接缝纪律不变:`coding_agent.py` 是唯一 Agent SDK 调用者、可 fake、整族离线;git 操作是**本地真执行**(无 SDK / 无网 / 无钱),离线测试用临时 git 仓库。
- 建在你刚修的 confine(软链根)之上;git 感知路径必须保持 confine 在真仓库根(含软链)上有效。
- **build 全程离线 + mock seam**(364 无回归判官不变);**绝不设 `ANTHROPIC_API_KEY`**(那是按量付费,与 6/15 计费切换无关)。
- **真 E2E 本期可由 session 跑**(10b-1 无 shell、低风险):用**订阅认证**(不设 key)、`run-once --workflow coding` 在**一次性工作区**、沿用有界(max_turns/tools/budget),报告 diff + turns/cost。**仍不合并——合并你本人来。**
- **无回归判官**:既有 364 测试逐字节绿(Config 回落 + 非 git 回落保 10a 行为);coding 增量;分支 `phase10b1`。

## 6. 流程(同前:计划 review 一道,然后一口气建完)
1. 代码侦察(10a 的 coding seam / family / runner coding 路径、Run schema + 触发控件、你刚修的 confine、测试环境 git 可用性)+ 出 **U1 详细计划 + U2 概要**(含 Run schema、per-run 流、git diff/还原做法)→ **停,等我 review 设计**,别直接建,flag 出入。
2. 批准后 **U1→U2 一口气建完**,每单元既有 364 测试原样绿当判官 + 新离线测试(fake seam + 临时 git 仓库)。建完 U1 贴 Run schema + per-run 流形状。全建完报告 + 停。
3. 全建完报告后,**session 可跑真 SDK 烟雾 E2E**:订阅认证(**不设 key**)、`run-once --workflow coding` 在一次性工作区、有界,报告 diff + turns/cost。**不合并——合并你本人来**(你也可自行再跑审 diff 流程的完整 E2E)。
