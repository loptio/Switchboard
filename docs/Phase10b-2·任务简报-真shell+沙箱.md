# Phase 10b-2 任务简报 —— 真 shell + 沙箱(单 agent 第一里程碑)

> 北极星(Phase 10):在 Switchboard 里跑会用工具、对世界动手、能互相对话的 coding agent。10b-1 让它能被指挥、对准真仓库、产出可审 diff,但**只能改文件、不能跑命令**(测试/构建/执行)。10b-2 给它**真 shell**——这是它"真能干活"的能力跃迁,也是**整条路最危险的一步**:任意命令执行。
>
> **第一里程碑(10b-2-1)**:Bash + 沙箱(Seatbelt 优先)+ 命令捕获/可审 + 有界/每命令超时 + 网络默认拒。容器级(CodeRunner)、网络细粒度放行、commit/PR、会话 UI、多 agent 留后段。

---

## 0. 这一关跟前面不同的纪律(先说,最重要)
- **沙箱借、不建**(蓝图决策 12):优先 Agent SDK 自带的 Seatbelt(macOS);不够再上 CodeRunner(VM 级)。**绝不自己手搓隔离。**
- **离线测试只能验"接线",验不了"关不关得住"**:沙箱是真 OS 机制,offline mock 不了。
- **本期 session 全程离线 + mock、不跑真 SDK**(**撤回 10b-1 给的 relaxation**):一个会跑真 shell 的 agent,不该由 session 无人值守地跑;而且沙箱在建造期还没经人验证过能不能关住。**所有真 E2E——"它真跑了命令"和"沙箱拦不拦得住逃逸"——都你本人亲手。** 这是 shell 带来的风险升级要求的。

## 1. 目标
- agent 能跑 shell 命令(测试/构建/lint/执行)——开 Bash 工具。
- 命令被**沙箱**关住:**文件系统锁在工作区、网络默认拒**。
- **命令可见**:捕获 agent 跑了哪些命令,审核时**连同 diff 一起给你看**(命令副作用**不在 diff 里**)。
- 有界不变 + **每条命令超时**(防挂死)。
- 10b-1 的安全全留:confine、clean-tree 前置、`.git` 拒写、审 diff、git 还原。

## 2. 范围(做 / 不做)
**做(10b-2-1)**:seam 开 Bash + 设沙箱策略(文件系统→工作区、网络拒)+ 捕获命令进 `CodingResult` + 每命令超时;审核面板/RunDetail 显示命令 + diff。
**不做**:CodeRunner VM 级(若 Seatbelt 够就先不上,recon 定)、网络细粒度放行、git commit/PR、会话 UI、多 agent。

## 3. 沙箱(核心决策,recon 落地)
- **首选:Agent SDK 自带的 Seatbelt**(macOS,Claude Code 同款)。recon 必须查清:**装的 SDK 版本怎么开沙箱、它到底限制什么**(文件系统能锁到 cwd 吗?网络能拒吗?Bash 工具受不受它管?)。
- **要求**:文件系统限制在工作区(**命令**也不能越界,不只文件工具)+ **网络默认拒**。
- 若 SDK 原生沙箱不够用/开不了 → 回退 **CodeRunner**(Apple Silicon 上 VM 隔离容器,经其 MCP 端点跑 agent 执行)或 Docker。**recon 给出实际可行的那条,计划 review 时锁定。**

## 4. 单元
- **U1 · Bash + 沙箱 + 命令捕获**:seam 开 Bash、设沙箱(文件系统→工作区 + 网络拒)、`CodingResult` 加 `commands`(捕获 Bash 调用)、每命令超时。**判官**:既有 386 测试原样绿(fake seam 不碰真 Bash;若有断言 `DEFAULT_CODING_TOOLS` 不含 Bash 的测试则更新)+ 新离线测试(沙箱配置设对、Bash 在工具集、命令被捕获、超时/有界逻辑)。
- **U2 · 命令可审**:审核 payload + RunDetail 显示命令(+ diff);人审 = 看 diff + 看跑了哪些命令再批/打回。**判官**:既有审核测试原样绿 + 新命令显示测试。
  - **(保险)** 建完 U1 贴沙箱配置(`ClaudeAgentOptions` 实际怎么设)+ `CodingResult.commands` 形状给我扫一眼;不停、续 U2。

## 5. 决策点
- **A · 沙箱 = Seatbelt 优先**(recon 验可行性)、CodeRunner 兜底;要求文件系统锁工作区 + 网络默认拒。默认按此。
- **B · 开 Bash 工具**(能力),沙箱当 containment。默认。
- **C · 命令可见**:捕获进 `CodingResult`、审核时连 diff 一起展示。默认(**必须**——命令副作用不在 diff 里)。
- **D · 每命令超时 + 现有有界**。默认(recon 查 SDK 是否支持命令级超时)。
- **E · 网络默认拒**(防外泄/下载执行),除非任务确需。默认。
- **F · 容器逃逸验证 = 你本人 hands-on**(离线 mock 不了);**session 本期不跑真 SDK**。默认(shell 的风险升级)。

## 6. 红线
- **沙箱借不建**。
- 接缝纪律不变:`coding_agent.py` 唯一 Agent SDK 调用者、可 fake、整族离线。
- **离线测试只验接线**(沙箱开了、命令捕获了、工具集含 Bash),**验不了 containment**——那是真 OS 沙箱、你 hands-on。
- 10a/10b-1 安全全留:confine、clean-tree、`.git` 拒写、有界、审 diff、git 还原。
- **网络默认拒**。
- **本期 session 全程离线 + mock、不设 `ANTHROPIC_API_KEY`、不跑真 SDK、不合并**(撤回 10b-1 relaxation——shell)。真 E2E + 合并你本人。
- **无回归判官**:既有 386 逐字节绿;分支 `phase10b2`。

## 7. 流程(同前,一处不同)
1. recon(10a/10b-1 的 seam/沙箱现状、装的 SDK 版本的沙箱/网络/命令超时能力、Bash 工具、命令捕获点、审核展示)+ 出 **U1 详细计划 + U2 概要**(含**沙箱具体怎么开、网络拒怎么设、命令捕获/展示、超时**)→ **停,等我 review 设计**,flag 出入。**沙箱实际机制 recon 落地后我重点看。**
2. 批准后 **U1→U2 一口气建完**,既有 386 逐字节绿当判官 + 新离线测试(fake seam)。建完 U1 贴沙箱配置 + `CodingResult.commands` 形状。报告 + 停。
3. **真 E2E + 沙箱逃逸验证 + 合并,全你本人**(session 本期不跑真 SDK)。

> 给你的 hands-on E2E(到时):跑一个需要命令的任务(如"加个函数并跑测试"),确认命令真跑了 + 被捕获 + 展示了;**然后试着让 agent 越界**(写工作区外、`curl` 外网、读环境变量/密钥)——**确认沙箱拦住**。这一步是这关的真验收,只有你能做。

---

## 8. 逃逸验收发现(real-machine,10b-2-1)

逃逸验收通过,沙箱 containment 站住(越界写 / 网络 / `.git` 全拦)。两处补丁 + 一条记录:

- **【阻断·已修】env 泄漏**:沙箱只管文件 + 网络,**不擦环境变量**;且 SDK 拼子进程 env 是 `{**os.environ, **options.env}`(**合并**,非替换),故沙箱里的 bash 会继承 worker 全量 env(`SECRET_KEY`/`SMTP_PASSWORD`/各 LLM key/`DATABASE_URL`),经模型通道或 diff 外泄(网络拒管不了)。**修(两轮)**:① 先试**最小白名单**(`PATH`/`HOME`/`LANG`/`LC_*`)→ 真机**断了订阅认证**(`Not logged in`,CLI 需要的 env 比白名单多,如 macOS 安全会话变量)。② 改为**denylist**(合并语义下你给的「pop 敏感键」路径):`_scrubbed_env()` 只把**名字含 `KEY`/`SECRET`/`TOKEN`/`PASSWORD`/…的键 + 显式 `DATABASE_URL`** 从 `os.environ` pop 掉(窗口内、`finally` 还原),CLI 其余 env 原样保留 → 认证不断、secrets 不进 bash。`SESSION`/`AUTH` 故意不做模式(那是 CLI 要的认证/会话变量)。
- **【显示·已修】**`commands` 捕获正确(在 `data` 里),但 `RunDetail` 原先只渲染 markdown、不显示命令 → 复用 `CodingDiff`,完成视图也显示 "Commands run" + `.git` 篡改横幅。
- **【记录·关系】`.git` 主防线在沙箱层**:实测 CLI 的 Seatbelt profile **本身就拒写 `.git/`**(`echo > .git/config` / `.git/hooks/pre-commit` 直接 "operation not permitted")。所以 worker 侧 `.git` 完整性检查(`git_security_*`)是**与 CLI 版本无关的兜底**(Seatbelt profile 是 CLI 内部细节、可能变),也是唯一**可离线验**的 `.git` 防线;**正常沙箱下 `git_tampered` 恒为 `[]` 是预期、不是 bug**(写 `.git` 先被沙箱拦,轮不到它)。
- **【约束·合并前确认,不卡本期】`_scrubbed_env()` pop 的是进程全局 `os.environ`、窗口 = 整个 agent 运行**——并发正确性约束(非泄漏)。**今天安全**:`run-once` 独立进程;scheduler worker **顺序执行**(`BlockingScheduler`、`max_instances=1`、单条 drain 循环)。**仅当** worker 改成并发(线程池 / `max_instances>1`)**且** coding run 与吃 env 的 run(如 digest 靠 `SMTP_PASSWORD` 发邮件)并发时,那个 run 会在窗口内读到被 pop 掉的 secret → 失败。届时(coding 进共享并发 worker)修:coding run 在 worker 内单飞,或把擦 env 做成**子进程级**、不动共享 `os.environ`。详见 `coding_agent._scrubbed_env` 注释。
