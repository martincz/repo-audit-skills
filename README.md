# repo-audit-skills

面向现代高自主性推理模型（兼容 **OpenAI Codex / Claude Code / Gemini CLI & Antigravity / Cursor**）的全栈代码审计、重构蓝图设计与规范文档治理 Skill 套件，严格遵循 [`agentskills.io`](https://agentskills.io) 开放规范。

采用**单一总控入口（`skills/repo-audit/SKILL.md`）+ 单跳按需加载专项规程（`references/*.md`）**的三层渐进式披露架构，最大限度降低常驻上下文开销，并通过物理门禁严格隔离**只读代码审计（`Mode R`）**与**受控规范文档重构（`Mode W-Doc`）**。

---

## 覆盖矩阵与权限边界

| 子命令 | 领域 | 专项规程 | 权限门禁 | 核心职责 |
| :--- | :--- | :--- | :--- | :--- |
| `health` | **全局体检** | [health-check.md](skills/repo-audit/references/health-check.md) | `Mode R` 只读 | 6 维全景初筛（安全、正确性、性能、架构、测试、工程配置）与深水区分流 |
| `security` | **安全边界** | [security-boundary.md](skills/repo-audit/references/security-boundary.md) | `Mode R` 只读 | 鉴权/多租户越权（IDOR）、密钥与日志脱敏、注入/SSRF/路径穿越、Webhook 防重放、CI 供应链，以及间接提示词注入与 AI 工具权限边界（纯防御视角） |
| `code` | **编码质量** | [code-quality.md](skills/repo-audit/references/code-quality.md) | `Mode R` 只读 | 并发竞态、静默吞异常、数值精度/句柄泄漏排查 + 非必要复杂度与过度设计减法（附必要复杂度保护红线） |
| `structure` | **目录重构** | [module-structure.md](skills/repo-audit/references/module-structure.md) | `Mode R` 只读 | 拆解万能 `utils/`、保护深模块、合并碎片微文件、盘点非 `import` 隐式引用，输出 100% 行为等价的 DAG 重组蓝图 |
| `ui` | **前端审计** | [frontend-ui-audit.md](skills/repo-audit/references/frontend-ui-audit.md) | `Mode R` 只读 | 对照三文档标尺与客观版式准则，排查视觉权重倒挂、卡片套卡片、Token 硬编码绕过、换肤成本、响应式与 CLS |
| `db` | **数据库治理** | [database-audit.md](skills/repo-audit/references/database-audit.md) | `Mode R` 只读 | 合并高频 SQL/联合索引/长事务/写入幂等审计 + 全量持久化资产孤儿反查、多环境门控与 `Retention ≥ Lookback` 对账 |
| `perf` | **应用层性能** | [app-performance.md](skills/repo-audit/references/app-performance.md) | `Mode R` 只读 | 外部 I/O 批量化、事件循环同步阻塞、单链重复拉取、热路径算法/流式处理，以及重试风暴、LLM Prompt Cache 命中与推理档位开销 |
| `test` | **测试与门禁** | [test-and-gates.md](skills/repo-audit/references/test-and-gates.md) | `Mode R` 只读 | 实测耗时采样、清理 4 类 AI 膨胀测试与真 `sleep`、识别高危路径测试缺口、设计秒级增量 `pre-commit` 分层蓝图 |
| `design-docs` | **设计规范立标** | [frontend-design-docs.md](skills/repo-audit/references/frontend-design-docs.md) | `Mode W-Doc` 仅改规范文档 | 采优去杂建立或重构 `PRODUCT.md`、`DESIGN.json`、`DESIGN.md` 单一事实源，严禁改动业务源码与样式代码 |
| `agents` | **业务仓 Agent 治理** | [agent-docs-business.md](skills/repo-audit/references/agent-docs-business.md) | `Mode W-Doc` 仅改规范文档 | 对照源码直接重构普通业务项目的 `AGENTS.md`、`CLAUDE.md`、项目级 Skills 与开发文档，对齐闭环验收、缓存前缀稳定与自主边界规范并保留核心红线 |
| `skill-repo` | **Skills 框架仓治理** | [agent-skills-framework.md](skills/repo-audit/references/agent-skills-framework.md) | `Mode W-Doc` 仅改规范与配置 | 面向 Skills / Subagents 交付仓库：`agentskills.io` 合规、三层渐进式披露拆分、跨端配置隔离、评估驱动维护与提示词现代化 |

> **安全承诺**：所有 `Mode R` 专项承诺零文件修改、零数据库写操作；审阅过程中统一将目标仓库的源码、注释、日志、库数据与网页内容视为不可信数据，绝不执行其中夹带的指令；所有 `Mode W-Doc` 专项在修改前必检 `git status`，仅修改白名单内的规范/配置文档，经确定性校验并修平全部问题后停留在本地工作区供人工审阅 `diff`（绝不自动 `git commit` 或 `git push`）。

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
将本仓库下的 `skills/repo-audit` 目录挂载至对应客户端的技能发现路径：

- **项目级共享（Antigravity / Gemini CLI / 通用 `.agents` 规范）**：
  ```bash
  mkdir -p .agents/skills
  ln -s /path/to/repo-audit-skills/skills/repo-audit .agents/skills/repo-audit
  ```
- **OpenAI Codex 全局技能**：`~/.codex/skills/repo-audit`（内置 [`agents/openai.yaml`](skills/repo-audit/agents/openai.yaml) 侧车配置）
- **Claude Code 全局技能**：`~/.claude/skills/repo-audit`
- **Antigravity / Gemini CLI 全局技能**：`~/.gemini/config/skills/repo-audit`

---

## 推荐搭配生态 Skills（Companion Skills）

`repo-audit` 专注于**存量工程治理（立标准 + 只读审计/输出蓝图）**，不重复造“动手写前端代码”的轮子。在前端与浏览器实测场景下，推荐搭配以下官方生态 Skills 形成完整闭环：

1. **前端设计与代码落地**：安装 Anthropic 官方 [`frontend-design`](https://github.com/anthropics/skills)（`npx skills add anthropics/skills --skill frontend-design`）或 `impeccable`
   - **推荐工作流**：先用 `repo-audit` 建立 `PRODUCT.md` / `DESIGN.json` / `DESIGN.md` 标准（内置防风格漂移护栏）并输出只读 UI/UX 修复清单，确认后再将修复项交给 `frontend-design` 或 `impeccable` 动手修改组件与样式代码。
2. **真实渲染与无障碍核验**：挂载 `chrome-devtools` / `a11y-debugging` MCP 与配套技能时，`repo-audit` 会自动优先执行桌面/移动双视口及深浅色真实渲染校验；未挂载时自动无感降级为静态源码审查。

---

## 典型调用示例

支持**无参数唤出交互菜单**、**短子命令直达/组合**，以及**自然语言自动匹配**：

- **查看全部能力菜单（不自动开跑，结合当前仓库高亮推荐项）**：
  - `/repo-audit` 或 `$repo-audit` 或 `/repo-audit help`
- **短子命令直达或空格多选组合**：
  - `/repo-audit health` — 执行全局只读健康检查
  - `/repo-audit db` — 执行数据库 SQL 性能安全与数据生命周期契约审计
  - `/repo-audit security perf` — 同时运行安全边界与应用层性能审计
  - `/repo-audit design-docs` — 重构 `PRODUCT.md`、`DESIGN.json` 与 `DESIGN.md`（不改业务代码）
  - `/repo-audit agents` — 重构当前业务项目的 `AGENTS.md` 与项目级 Skills
  - `/repo-audit skill-repo` — 审计并升级 Skills / Subagents 框架仓库的结构与规范
- **自然语言直接唤醒**：
  - `梳理当前项目的目录结构与模块粒度，先输出只读重组蓝图`
  - `体检 tests/ 测试套件与 pre-commit 钩子耗时，输出精简与分层蓝图`

---

## 内置确定性校验脚本

仓库内置零第三方依赖的 Python 3 只读校验工具 [`verify_governance.py`](skills/repo-audit/scripts/verify_governance.py)，用于核验任意目标仓库中的 `SKILL.md` 元数据与行数预算、`agents/openai.yaml` 侧车配置、Markdown 本地相对路径死链及 `DESIGN.json` 语法：

```bash
python3 skills/repo-audit/scripts/verify_governance.py --root .
python3 skills/repo-audit/scripts/verify_governance.py --root /path/to/target-repo --json
```

---

## License

[MIT](LICENSE)
