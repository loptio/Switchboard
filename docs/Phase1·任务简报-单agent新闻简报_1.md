# Phase 1 · 任务简报:单 agent 新闻简报

> **给执行这份简报的 build session:**
> 你要建的是整个系统的**第一片**(Phase 1)。完整设计见《Agent 系统 · 架构与建造蓝图》——那是单一真相来源,本简报与它冲突时以蓝图为准。
> **人类是 orchestrator**:范围之外的东西不要自作主张去加;有不清楚的地方,问人,别擅自扩范围。

---

## 1. 目标(一句话)

做一条命令:**抓取一个 RSS 源 → 用 Claude agent 总结 → 把简报写成一个本地 markdown 文件(并打印到控制台)。**

## 2. 范围

**做(In):**
- 抓取**一个** RSS 源(URL 可配置)
- 用 **Claude Agent SDK(Python)** 让 agent 把条目总结成简报
- 产出写成本地文件 `output/digest-YYYY-MM-DD.md`,同时打印到控制台
- 本地 Git 仓库 + 基本工程规范(见第 6 节)

**明确不做(Out)——别加:**
- ❌ 数据库(Phase 2)
- ❌ 推送通知(Phase 2)
- ❌ 定时调度(Phase 2)
- ❌ 前端 / Web(Phase 3)
- ❌ 多厂商模型 / LangGraph(后期)
- ❌ 认证、多 agent 编排、meta-agent(后期)

## 3. 已定决策(不要再改)

- **来源**:RSS,先一个源。默认 `https://hnrss.org/frontpage`(若人类另给了 URL,用人类给的)。
- **职责切分**:**抓取用代码(确定性),理解/总结用 agent**。
- **产出**:本地 markdown 文件 + 控制台,**不入库**。

## 4. 模块契约(概念接口,要遵守——这是为后续阶段留的缝)

把代码切成三块 + 一个入口,边界清晰:

- **fetch 模块**:输入 = feed URL;输出 = 条目列表,每条字段 `{ title, link, summary, published }`。纯代码(用 feedparser 之类),**不调 agent**。
- **agent 模块**:输入 = 条目列表;输出 = 简报(结构化:前 N 条,每条 `{ title, link, one_line_summary }`,或一段 markdown)。用 Claude Agent SDK。
- **output 模块**:输入 = 简报;写文件 + 打印控制台。
- **入口(main)**:把三者串起来 `fetch → agent → output`。

**配置**(feed URL、取几条 N、输出目录、模型名)放在配置文件或环境变量里,**不要硬编码**。

## 5. 技术约束

- **语言**:Python(≥3.10)。
- **agent**:Claude Agent SDK(Python)。按官方文档认证;**不要设置 `ANTHROPIC_API_KEY` 环境变量**,以免意外走 API 计费——用订阅/额度认证。
- **RSS**:feedparser(或同类)。
- 保持**单一小项目**,别提前抽象、别过度设计(YAGNI)。
- 密钥/敏感配置走环境变量,不进代码、不进 Git。

## 6. 工程规范(顺便在练阶段 1)

- **Git**:本地仓库;小步、勤提交,提交信息写清**为什么**。
- **测试**:至少给 **fetch 模块**写一个基本测试(给定一段示例 RSS,能正确解析出条目列表)。agent 那块可先不强求自动化测试。
- **README**:写清怎么装、怎么跑、产出在哪。
- 代码清楚优先于聪明。

## 7. 验收标准(做到这些才算完成)

1. 跑**一条命令**,在 `output/` 下生成当天日期的 markdown 简报文件。
2. 简报含前 N 条,每条:一句话总结 + 原文链接。
3. 控制台也打印出该简报。
4. fetch / agent / output 三块按第 4 节的接口分开,各司其职。
5. feed URL、N 等是**可配置**的,不硬编码。
6. fetch 模块有一个能跑过的基本测试。
7. 有 README;代码已提交进本地 Git(若干条有意义的 commit)。

## 8. 交付物

- 可运行的项目骨架(含上述三模块 + 入口)
- 一份示例产出文件 `output/digest-YYYY-MM-DD.md`
- fetch 模块的基本测试
- README
- 本地 Git 提交历史

---

> 完成后,人类(orchestrator)会 review 并验收,再决定进入 Phase 2(DB = 单一真相来源 + 调度 + 推送)。
