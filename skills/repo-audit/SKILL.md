---
name: repo-audit
description: >-
  Full-spectrum repository audit, refactoring blueprint, and Agent/Design documentation
  governance suite covering health checks, security boundaries, code quality & complexity,
  module structure, frontend UI/UX & design tokens, database SQL & data lifecycle, application
  performance, test suites & pre-commit gates, and AGENTS.md/Skills governance. Use when
  the user asks to audit, review, inspect, diagnose, check repo health, output module/test
  refactoring blueprints, or standardize PRODUCT.md/DESIGN.md/DESIGN.json/AGENTS.md/SKILL.md
  (当用户要求进行项目体检、编码/安全/前端/SQL/数据生命周期/性能/测试/目录结构只读审计，或重构
  AGENTS.md、Skills、设计规范与项目文档时使用；日常普通业务功能开发不触发).
license: MIT
metadata:
  version: "1.0.0"
---

# Repo Audit & Documentation Governance (`repo-audit`)

本 Skill 提供面向现代高自主性推理模型（Codex / Claude / Gemini 等）的全栈代码审计、重构蓝图设计与规范文档治理工作流。以当前 `SKILL.md` 所在目录作为 `<skill-dir>`，按需读取下方 `references/` 中的专项规程执行任务。

## 1. 意图路由与按需加载（Progressive Disclosure）

### 无参数或帮助发现（Menu Mode）
当用户仅输入 `/repo-audit`、`$repo-audit`、`/repo-audit help` / `list` 或询问“有哪些功能/专项”时，**不自动执行任何审计或修改**。请先快速扫描目标仓库顶层目录特征（如是否含前端代码、数据库迁移、`tests/`、`AGENTS.md` 或 `SKILL.md`），然后直接输出按 `Mode R`（只读 8 项）与 `Mode W-Doc`（改文档 3 项）分组的精简能力菜单（含编号、短子命令、权限门禁与一句话职责），顺手标出最适合当前仓库的 2～3 项推荐，提示用户回复编号、短子命令或自然语言即可启动。

### 短子命令与自然语言路由表
根据用户子命令或自然语言诉求匹配下表，**仅读取当次任务命中的 1～2 份参考文件**，不预加载无关专项：

| 子命令 | 用户意图 / 审计领域 | 权限门禁 | 按需读取的专项规程（相对 `<skill-dir>`） |
| :--- | :--- | :--- | :--- |
| `health` | 全局项目体检、未知风险初筛、排期摸底 | `Mode R`（严格只读） | [references/health-check.md](references/health-check.md) |
| `security` | 安全漏洞、鉴权/多租户越权、密钥脱敏、注入/SSRF、供应链与 AI 工具边界 | `Mode R`（严格只读） | [references/security-boundary.md](references/security-boundary.md) |
| `code` | 编码正确性、并发/异常/精度边界、非必要复杂度与过度设计减法 | `Mode R`（严格只读） | [references/code-quality.md](references/code-quality.md) |
| `structure` | 代码目录归属、巨石文件拆分、微文件合并、单向 DAG 依赖重组蓝图 | `Mode R`（严格只读） | [references/module-structure.md](references/module-structure.md) |
| `ui` | Web 页面 UI/UX、视觉层级倒挂、样式耦合、响应式与换肤成本审计 | `Mode R`（严格只读） | [references/frontend-ui-audit.md](references/frontend-ui-audit.md) |
| `db` | 数据库 Schema/迁移、高频 SQL/索引/事务幂等、全量数据生命周期与保留契约 | `Mode R`（严格只读） | [references/database-audit.md](references/database-audit.md) |
| `perf` | 应用层热路径性能、外部 API/I/O 批量化、事件循环阻塞、内存与 LLM 配额效率 | `Mode R`（严格只读） | [references/app-performance.md](references/app-performance.md) |
| `test` | 测试套件去重瘦身、高危路径测试缺口、秒级增量 `pre-commit` 与门禁分层蓝图 | `Mode R`（严格只读） | [references/test-and-gates.md](references/test-and-gates.md) |
| `design-docs` | 建立或重构前端产品与设计规范文档（`PRODUCT.md` / `DESIGN.json` / `DESIGN.md`） | `Mode W-Doc`（仅改规范文档） | [references/frontend-design-docs.md](references/frontend-design-docs.md) |
| `agents` | 重构普通业务仓库的 `AGENTS.md`、`CLAUDE.md`、项目级 Skills 与开发指导文档 | `Mode W-Doc`（仅改规范文档） | [references/agent-docs-business.md](references/agent-docs-business.md) |
| `skill-repo` | 审计并升级以 Skills、Prompts、Subagents 为交付产物的框架/工具仓库 | `Mode W-Doc`（仅改规范与包配置） | [references/agent-skills-framework.md](references/agent-skills-framework.md) |

**路由补充规则**：
- 支持短子命令直达（如 `/repo-audit db`）、空格组合多专项（如 `/repo-audit security db`）以及自然语言自动匹配。
- 仅在用户明确表达全局体检诉求（如 `/repo-audit health` 或“全面审一下这个项目”）时运行 `references/health-check.md`；无参数调用一律先展示菜单等待选择。
- 用户当次明确指令优先于默认流程（例如在 `Mode W-Doc` 专项中用户声明“只读评估先别改文件”，则降级为 `Mode R`），但任何指令不得突破下方安全红线。

---

## 2. 双模式权限门禁（Permission Gates）

<permission_gate>
默认安全落点为 **Mode R**。仅当用户当次请求明确要求创建、重构或升级治理/设计规范文档且命中上表最后三项时，才进入 **Mode W-Doc**。

### Mode R：严格只读模式（8 份审计与蓝图专项）
- **零仓库修改**：不创建、编辑、移动或删除任何源码、配置、迁移脚本、测试、钩子或文档（保持工作区干净以便后续分批落地与基准对比）。
- **零外部副作用**：不对任何数据库执行 DDL/DML 写操作；不向真实外部 API 发送写请求；不读取或输出真实用户密钥、会话与隐私明文。
- **合成基准边界**：如需验证算法复杂度或局部逻辑，仅在沙箱内存中运行纯合成数据的无状态只读命令。

### Mode W-Doc：受控文档修改模式（3 份规范治理专项）
- **写前基线检查**：动手修改前先运行 `git status`。若目标文档本身存在未提交的无关改动且有覆盖冲突风险，先提示确认或确保改动严格隔离。
- **严锁文件白名单**：只允许修改对应专项列明的目标文档/Skill 配置文件；**绝不顺手修改业务源码、页面模板、组件、JS/TS、CSS/Tailwind、测试用例或数据库迁移脚本**（防止在立标准阶段混入未确认的代码行为变更）。
- **仓库边界隔离**：可只读检查宿主全局配置或外部已安装 Skills 排查冲突，但只报告冲突，不修改目标仓库目录之外的任何文件。
- **确定性收尾校验与停靠**：修改完成后，优先运行 `python3 <skill-dir>/scripts/verify_governance.py --root <target-repo-root>` 核验元数据与相对路径有效性（若环境无 Python 3，则手动逐条核对引用路径与 JSON 语法），修复发现的问题后停留在本地工作区供人工审阅 `diff`，**不自动执行 `git commit` 或 `git push`**。
</permission_gate>

---

## 3. 侦察、工具联动与持续自主性（Execution & Persistence）

<execution_protocol>
1. **两步法消除上下文浪费**：
   - **第一步（轻量全局盘点）**：并行调用文件列表、配置读取与关键字检索，快速摸清目录拓扑、技术栈、核心入口与既有规则文档（如 `AGENTS.md`）。
   - **第二步（沿核心链路按需深读）**：聚焦高频接口、核心业务域、公共抽象层与高风险边界深入阅读源码，避免无差别通读冷门静态文件。
2. **并行调用与只读子智能体分流**：无依赖的文件读取、检索与符号查询一律在同一轮并行发起。在大中型仓库或多维联合审计中，若宿主环境提供只读探索子智能体（如 `research` / `Explore` subagent），可按独立子系统或检查维度并行委派侦察，在主上下文汇总去重。
3. **专业工具优先 + 无缝自动降级（不中途停等）**：
   - 代码符号与依赖图：若已连接 `Serena`、`codebase-memory-mcp` 或 LSP 工具，优先用于追踪调用链、扇入/扇出与引用点。
   - 数据库 / 浏览器 / 本地测试：按专项规程优先使用只读库工具（如 `DBHub`）、浏览器调试工具（如 `Chrome DevTools` / Playwright）或安全沙箱测试命令。
   - **降级契约**：当上述可选工具未配置、目标服务未启动或受沙箱限制时，在报告开头的「审计范围与验证边界」中如实记录一次，随即**自动降级为基于源码/配置/迁移脚本的静态审查并一次跑完整个任务**，不要中途停下询问“是否降级”。
</execution_protocol>

---

## 4. 证据准则、防过度工程与通用输出契约（Output Contract）

<output_contract>
所有 `Mode R` 审计报告（专项规程另有专属必输表格的，在其基础上叠加遵循）统一满足以下标准：

1. **审计范围说明（报告开头必含）**：用 3～5 行简明交代本次基于的分支/提交号、覆盖模块范围、实际调用的动态验证工具（或静态降级原因）及未覆盖边界。
2. **证据驱动与反幻觉铁律**：
   - 每条结论必须附上真实读取到的 `文件路径 / 模块或函数符号`（必要时附行号或表名/路由）。
   - 统一使用证据来源标签：`[已复现]`（通过安全只读脚本或渲染复现了缺陷）、`[已实测]`（经真实库元数据/`EXPLAIN`、测试计时或浏览器计算样式确认）、`[静态推断]`（基于源码调用链与配置逻辑推导）。
   - **禁止编造数字**：未实测时不得杜撰毫秒耗时、扫描行数、`EXPLAIN` 执行计划或测试覆盖率百分比，仅给出复杂度或调用放大公式（如 $O(N)$ 次单查 vs $O(1)$ 次批查）。
3. **高信噪比与反过度工程校准**：
   - 每个检查维度输出 **0～3 条**最具价值的实质问题；不足 3 条按实际输出，**不凑数**；无明显隐患直接写「未发现明显高价值问题」。
   - **推荐最小惯用改法**：改进方案优先复用项目现有依赖与语言标准库，保持改动局部化，避免为解决简单问题建议新建重型抽象层、工厂模式或多余配置开关。
   - **正反例校准**：
     - ✅ 应报告：跨租户查询漏传 `tenant_id`、`async def` 内调用同步阻塞 HTTP 导致事件循环卡死、保留期 7 天短于下游 30 天回看窗口。
     - ❌ 忽略不报：纯个人命名/缩进风格偏好、无性能差异的冷路径语法糖改写、主观审美配色喜好。
4. **标准单条问题格式**（专项有定制字段时按专项微调）：
   - **问题标题** `[已复现]` / `[已实测]` / `[静态推断]`
   - **涉及位置**：具体文件路径 / 函数或组件符号 / 表名或路由
   - **现状与风险（或放大效应）**：简明指明代码级问题机制及对安全、正确性、延迟或维护的具体影响
   - **改进方案与验证方法**：给出可落地的收敛方案，并附 1 条最关键的回归单测、只读核验命令或基准验证场景
   - **预估改动成本**：低 / 中 / 高
5. **收尾分阶段行动清单（Markdown 表格）**：
   综合「风险/收益 × 改动成本」，按以下四档排序输出汇总表（同档内按推荐执行先后排列，每项一句话写明**排序理由**与**建议最先下手的文件/函数**）：
   - **P0（立即处理）**：高危安全越权、数据损坏/误删、线上阻塞/雪崩或已复现的严重缺陷
   - **P1（近期收敛）**：高收益的核心性能瓶颈、关键架构耦合、高频慢查询或高风险测试真空
   - **Quick Win（顺手修复）**：改动极小、几乎零语义风险且立竿见影的局部加固或瘦身
   - **P2（长期演进）**：跨模块结构演进、全局主题/归档体系升级或低频长尾优化
</output_contract>
