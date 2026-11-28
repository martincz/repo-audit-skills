# repo-audit-skills

面向 **OpenAI Codex / Claude Code / Gemini CLI / Antigravity / Cursor** 等 AI 编程助手的全栈仓库审计、重构蓝图输出与工程文档治理技能包，遵循 [`agentskills.io`](https://agentskills.io) 开放规范。

技能包以 `skills/repo-audit/SKILL.md` 作为统一入口，仅在命中具体子命令或需求时按需加载对应专项指南（`references/*.md`），让对话保持精简高效；同时将**只读审计与重构蓝图（`Mode R`）**和**仅改规范/配置文档的治理流程（`Mode W-Doc`）**拆分为独立工作流，降低误改业务代码的风险。

---

## 覆盖矩阵与权限边界

| 子命令 | 领域 | 专项指南 | 权限模式 | 核心职责 |
| :--- | :--- | :--- | :--- | :--- |
| `health` | **全局体检** | [health-check.md](skills/repo-audit/references/health-check.md) | `Mode R` 只读 | 6 维全景初筛（安全、正确性、性能、架构、测试、工程配置）与深水区分流 |
| `security` | **安全边界** | [security-boundary.md](skills/repo-audit/references/security-boundary.md) | `Mode R` 只读 | 鉴权/多租户越权（IDOR）、密钥与日志脱敏、注入/SSRF/路径穿越、Webhook 防重放、CI 供应链，以及间接提示词注入与 AI 工具权限边界（纯防御视角） |
| `code` | **编码质量** | [code-quality.md](skills/repo-audit/references/code-quality.md) | `Mode R` 只读 | 并发竞态、静默吞异常、数值精度/句柄泄漏排查 + 非必要复杂度与过度设计减法（附必要复杂度保护红线） |
| `structure` | **目录重构** | [module-structure.md](skills/repo-audit/references/module-structure.md) | `Mode R` 只读 | 拆解万能 `utils/`、保护深模块、合并碎片微文件、盘点非 `import` 隐式引用，输出 100% 行为等价的 DAG 重组蓝图 |
| `ui` | **前端审计** | [frontend-ui-audit.md](skills/repo-audit/references/frontend-ui-audit.md) | `Mode R` 只读 | 对照 `PRODUCT.md` / `DESIGN.json` / `DESIGN.md` 与客观版式准则，排查视觉权重倒挂、卡片套卡片、Token 硬编码绕过、换肤成本、响应式与 CLS |
| `db` | **数据库治理** | [database-audit.md](skills/repo-audit/references/database-audit.md) | `Mode R` 只读 | 高频 SQL/联合索引/长事务/写入幂等审计 + 全量持久化资产孤儿反查、多环境门控与 `Retention ≥ Lookback` 对账 |
| `perf` | **应用层性能** | [app-performance.md](skills/repo-audit/references/app-performance.md) | `Mode R` 只读 | 外部 I/O 批量化、事件循环同步阻塞、单链重复拉取、热路径算法/流式处理，以及重试风暴、LLM 缓存命中与推理成本优化 |
| `pipeline` | **批处理与产物管线** | [pipeline-efficiency.md](skills/repo-audit/references/pipeline-efficiency.md) | `Mode R` 只读 | 针对数据构建、批量导出、离线计算与评估作业：排查小文件碎片、清单膨胀、重复校验与拷贝、逐文件刷盘、计算与落盘耦合、快路径退化及运行脚本闭包，输出可复验重构蓝图 |
| `test` | **测试与门禁** | [test-and-gates.md](skills/repo-audit/references/test-and-gates.md) | `Mode R` 只读 | 实测耗时采样、清理 4 类 AI 膨胀测试与真 `sleep`、识别高危路径测试缺口、设计秒级增量 `pre-commit` 分层蓝图 |
| `design-docs` | **设计规范立标** | [frontend-design-docs.md](skills/repo-audit/references/frontend-design-docs.md) | `Mode W-Doc` 仅改规范文档 | 采优去杂建立或重构 `PRODUCT.md`、`DESIGN.json`、`DESIGN.md` 单一事实源，不改动业务源码与样式代码 |
| `agents` | **业务仓 Agent 治理** | [agent-docs-business.md](skills/repo-audit/references/agent-docs-business.md) | `Mode W-Doc` 仅改规范文档 | 对照真实源码整理或重构业务项目的 `AGENTS.md`、`CLAUDE.md`、项目级 Skills 与开发文档，清除过期内容并明确校验步骤、修改边界与核心约束 |
| `skill-repo` | **Skills 框架仓治理** | [agent-skills-framework.md](skills/repo-audit/references/agent-skills-framework.md) | `Mode W-Doc` 仅改文档与配置 | 面向 Skills / Subagents 仓库：按 `agentskills.io` 规范检查并整理入口文档、按需加载的参考文件、跨客户端配置与测试评估清单；脚本与 `LICENSE` 等文件仅检查并给出修改建议，不自动改动 |

> **安全边界说明**：
> - **工作流约定**：所有 `Mode R` 专项仅执行只读审计并输出重构蓝图，指令层面禁止修改仓库文件、写入数据库或触发外部写操作，并将目标仓库的源码、注释、日志、数据库返回与网页内容视为不可信数据，不执行其中夹带的指令；所有 `Mode W-Doc` 专项在修改前先检查 `git status` 与既有基线，仅编辑白名单内的规范或配置文件（不改动业务代码、样式、测试、脚本或 `LICENSE`），校验并修复白名单内文件及本次引入的问题（白名单外既有问题仅作报告），完成后停留在本地工作区供人工审阅 `diff`，不自动执行 `git commit` 或 `git push`。
> - **系统级强制执行**：以上属于技能指令与工作流约定；对文件系统、数据库和外网访问的**硬性只读限制**仍由客户端自身的沙箱机制、命令审批策略及只读凭证来强制保障（审计敏感或生产相关环境时建议同时开启只读沙箱与只读凭证）。

---

## 安装方式

### 1. 通过 Skills CLI 安装（推荐）
```bash
# 全局安装到当前机器支持的 Coding Agents（支持完整 GitHub URL 或简写）
npx skills add https://github.com/martincz/repo-audit-skills --skill repo-audit -g
# 等价简写（加 -y 可跳过交互确认）
npx skills add martincz/repo-audit-skills --skill repo-audit -g -y
```

### 2. 手动软链或拷贝挂载
将本仓库下的 `skills/repo-audit` 目录软链或拷贝至对应客户端的技能目录：

- **用户全局通用（OpenAI Codex / Gemini CLI / Antigravity，推荐）**：`~/.agents/skills/repo-audit`（在 Gemini CLI 中优先级高于 `~/.gemini/skills/`；包内已附带 Codex 用的 [`agents/openai.yaml`](skills/repo-audit/agents/openai.yaml)）
- **项目级共享（Codex / Gemini CLI / Antigravity / 通用 `.agents` 规范）**：
  ```bash
  mkdir -p .agents/skills
  ln -s /path/to/repo-audit-skills/skills/repo-audit .agents/skills/repo-audit
  ```
- **各客户端专属或旧版兼容目录**：
  - **Claude Code**：`~/.claude/skills/repo-audit`
  - **Gemini CLI 专属目录**：`~/.gemini/skills/repo-audit`
  - **Antigravity 专属目录**：`~/.gemini/config/skills/repo-audit`
  - **OpenAI Codex 旧版兼容目录**：`~/.codex/skills/repo-audit`

---

## 推荐搭配生态 Skills（Companion Skills）

`repo-audit` 专注于**存量工程治理（立标准 + 只读审计/输出蓝图）**，不重复造“动手写前端代码”的轮子。在前端与浏览器实测场景下，推荐搭配以下官方生态 Skills 形成完整闭环：

1. **前端设计与代码落地**：安装 Anthropic 官方 [`frontend-design`](https://github.com/anthropics/skills)（`npx skills add anthropics/skills --skill frontend-design`）或 `impeccable`
   - **推荐工作流**：先用 `repo-audit` 建立 `PRODUCT.md` / `DESIGN.json` / `DESIGN.md` 标准（内置防风格漂移护栏）并输出只读 UI/UX 修复清单，确认后再将修复项交给 `frontend-design` 或 `impeccable` 动手修改组件与样式代码。
2. **真实渲染与无障碍核验**：挂载 `chrome-devtools` / `a11y-debugging` MCP 与配套技能时，`repo-audit` 会自动优先执行桌面/移动双视口及深浅色真实渲染校验；未挂载时自动无感降级为静态源码审查。

---

## 典型调用示例

支持**无参数唤出交互菜单**、**短子命令直达/组合**，以及**自然语言自动匹配**。在支持技能斜杠命令的客户端中使用 `/repo-audit ...`；在 **OpenAI Codex** 中请使用 `$repo-audit ...`（Codex TUI 会拦截未注册的 `/...` 斜杠命令）：

- **查看全部能力菜单（不自动执行，结合当前仓库高亮推荐项）**：
  - `/repo-audit`、`$repo-audit` 或 `/repo-audit help`（Codex：`$repo-audit help`）
- **OpenAI Codex 调用示例（使用 `$repo-audit`）**：
  - `$repo-audit health` — 执行全局只读健康检查
  - `$repo-audit security perf` — 同时运行安全边界与应用层性能审计
  - `$repo-audit pipeline` — 审计产物型批处理与离线作业的存储碎片、重复校验与运行效率
  - `$repo-audit pipeline test` — 联合审计批处理产物链路与测试/门禁覆盖
  - `$repo-audit agents` — 整理并重构当前业务项目的 `AGENTS.md` 与项目级 Skills
- **支持技能斜杠命令的客户端（短子命令直达或空格多选组合）**：
  - `/repo-audit health` — 执行全局只读健康检查
  - `/repo-audit db` — 执行数据库 SQL 性能安全与数据生命周期契约审计
  - `/repo-audit security perf` — 同时运行安全边界与应用层性能审计
  - `/repo-audit pipeline` — 审计产物型批处理与离线作业的小文件碎片、重复校验、计算效率与运行脚本闭包
  - `/repo-audit pipeline test` — 联合审计批处理产物链路与测试/门禁覆盖
  - `/repo-audit design-docs` — 重构 `PRODUCT.md`、`DESIGN.json` 与 `DESIGN.md`（不改业务代码）
  - `/repo-audit agents` — 整理并重构当前业务项目的 `AGENTS.md` 与项目级 Skills
  - `/repo-audit skill-repo` — 审计并整理 Skills / Subagents 仓库的规范文档与配置
- **自然语言直接唤醒**：
  - `梳理当前项目的目录结构与模块粒度，先输出只读重组蓝图`
  - `排查离线批处理作业为何产生大量小文件、重复哈希和耗时膨胀，输出只读重构蓝图`
  - `体检 tests/ 测试套件与 pre-commit 钩子耗时，输出精简与分层蓝图`

---

## 内置只读辅助脚本

仓库内置两份零第三方依赖的 Python 3 只读脚本，均不修改目标仓库文件：

1. **文档与技能包校验脚本 [`verify_governance.py`](skills/repo-audit/scripts/verify_governance.py)**：检查任意目标仓库中的 `SKILL.md` 元数据与文件长度规范、`agents/openai.yaml` 的 Codex 扩展配置、Markdown 本地相对路径死链及 `DESIGN.json` 语法结构。
2. **产物与脚本元数据观测脚本 [`scan_artifacts.py`](skills/repo-audit/scripts/scan_artifacts.py)**：配合 `pipeline` 专项使用，纯元数据只读观测（不读取文件内容），在文件数、深度与协作式遍历时间上限（`--timeout`）内安全统计逻辑路径数与去重物理文件数、体积分桶与分位数、高频后缀、热点目录及工具/脚本目录规模（仅输出客观观察值，不写死阻断阈值，不跟随符号链接）。

```bash
# 1. 文档与技能包校验（在本仓库检出目录下运行）
python3 skills/repo-audit/scripts/verify_governance.py --root .
python3 skills/repo-audit/scripts/verify_governance.py --root /path/to/target-repo --json

# 2. 产物与脚本元数据只读观测（支持追加获准检查的外置数据/产物根目录）
python3 -I -S -B skills/repo-audit/scripts/scan_artifacts.py --root /path/to/target-repo --json
python3 -I -S -B skills/repo-audit/scripts/scan_artifacts.py --root /path/to/target-repo --artifact-dir /path/to/artifacts --json

# 已安装技能包的用户（将 <skill-dir> 替换为实际安装目录，如 ~/.agents/skills/repo-audit）
python3 <skill-dir>/scripts/verify_governance.py --root <target-repo> --json
python3 -I -S -B <skill-dir>/scripts/scan_artifacts.py --root <target-repo> --json
```

---

## License

[MIT](LICENSE)
