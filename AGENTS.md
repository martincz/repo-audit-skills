# Repository Guidelines (`repo-audit-skills`)

## 1. 元仓库角色声明（三角色隔离，防止指令串扰）

本仓库的交付产物是符合 `agentskills.io` 开放规范的 Agent Skill 包（位于 `skills/repo-audit/`）。
- **三角色边界隔离**：`skills/repo-audit/` 是可独立分发安装到**下游目标仓库**的交付包，其 `SKILL.md` 与 `references/*.md` 不得引用或依赖本仓根目录的 `AGENTS.md` / `README.md`；根目录 `AGENTS.md` 仅面向开发维护本仓的 Coding Agents；根目录 [README.md](README.md) 是**面向人类用户的产品手册与门面说明**，统一使用通俗准确的用户语言，不向其中泄漏内部提示词工程或维护者术语。
- **维护角色优先**：`skills/repo-audit/` 内的规程面向**下游目标仓库**执行；当你在本仓库内开发维护时，你的职责是**编写与演进这些 Skill 规程及校验脚本**，不受子文档中“目标仓只读零修改”条款对本仓自身的限制（除非用户明确要求对本仓运行自测审计）。
- **按需读取**：修改具体专项时仅读取 [SKILL.md](skills/repo-audit/SKILL.md) 与对应 `skills/repo-audit/references/*.md`，无需预先通读全部参考文件。

## 2. 核心架构与合规红线（不可破坏的不变量）

1. **权限门禁按文件隔离（`Mode R` 与 `Mode W-Doc` 分立独立文件）**：
   - `Mode R`（只读审计与可执行蓝图专项）在规程层面保持零文件修改、零写库及零外部写副作用，绝不混入直接改业务源码、改测试或改样式的指令；
   - `Mode W-Doc`（规范文档治理专项）仅允许修改列明的目标规范文档或 Skill 配置文件，不在同一份 `references/*.md` 中混写冲突的读写规则（硬性文件/数据库/外网阻断由客户端沙箱、命令审批与只读凭证承担，按文件分离用于避免下游模型因权限矛盾误改用户代码）。
2. **三层渐进式披露与单跳拓扑**：
   - 根入口 [SKILL.md](skills/repo-audit/SKILL.md) 只保留意图路由、双模式门禁与全局共享协议（控制在 250 行以内，硬上限 `<500` 行，降低常驻上下文开销）；
   - 所有专项规程保持 `SKILL.md → references/*.md` 的 **1 层单跳扁平引用**，避免跨参考文件链式跳转导致按需加载遗漏或上下文膨胀；
   - 指令中引用内置脚本时统一基于 `<skill-dir>/scripts/...` 解析路径，并提供缺少 Python 环境时的静态降级路径，不假设目标项目工作目录在本仓。
3. **跨客户端元数据与 MIT 协议纯净性**：
   - `SKILL.md` 的 YAML frontmatter 仅使用 `agentskills.io` 标准字段（`name`、`description`、`license`、`compatibility`、`metadata`、`allowed-tools`），遵循开放规范并保障严格校验器（如 `skills-ref`）及跨客户端可移植性；
   - OpenAI Codex 专属展示与调用策略统一维护在 [openai.yaml](skills/repo-audit/agents/openai.yaml)（`interface.short_description` 保持 25～64 字符，`default_prompt` 显式包含 `$repo-audit`）；
   - [verify_governance.py](skills/repo-audit/scripts/verify_governance.py) 保持**纯 Python 3 标准库、零第三方依赖、无交互阻塞**；
   - 严守 [LICENSE](LICENSE)（MIT）纯净性：`skills/repo-audit/LICENSE` 与根目录 [LICENSE](LICENSE) 保持逐字节同步，确保独立分发拷贝携带完整 MIT 许可声明；外部非 MIT 生态技能（如 `frontend-design`）仅采取运行时探测协同 + 极简客观规则兜底，绝不直接拷贝第三方受限文本。
4. **提示词工程风格基线**：
   - 面向高自主性推理模型（Codex / Claude / Gemini）编写：常规规则使用清晰直接的肯定句与一致的结构分隔，仅在真实安全/只读/防数据丢失红线处使用强约束并附简短原因；专项规程统一使用语义 XML 标签 `<permission_gate>`、`<output_contract>`、`<completion_criteria>`（[SKILL.md](skills/repo-audit/SKILL.md) 另含 `<execution_protocol>`）；
   - 不要求在正文输出原始逐步推理过程，推理深度由宿主运行时控制；移除冗余的泛化简短催促与易诱发保守漏报的刚性截断/机械复查话术；
   - 保持静态规则在前、动态输入在后的布局（具体缓存与推理连续性以各模型/API 实测为准），明确闭环验收标准、自主决策边界与不可信数据隔离，不将单一行业特定术语写死为通用默认前提（详见 [agent-skills-framework.md](skills/repo-audit/references/agent-skills-framework.md)）。

## 3. 跨文件同步契约

新增、合并、重命名或删除 `skills/repo-audit/references/*.md` 专项文件或调整子命令别名时，同步更新以下两处（技能包变更与元仓库文档变更保持职责清晰，按需拆分为独立提交）：
1. [SKILL.md](skills/repo-audit/SKILL.md) 的 `description` 触发范围（保持 ≤1024 字符，并复核正向触发与“不误触日常普通业务开发”的负向边界）及第 1 节短子命令意图路由表；
2. [README.md](README.md) 的「覆盖矩阵与权限边界」表格与调用示例。

## 4. 指令优先级、自主边界、提交规范与验收闭环

- **指令优先级**：`核心架构与合规红线（第 2 节）` > `用户当次明确指令` > `本仓默认工作流`（例如用户明确要求提交时可覆盖“默认停留在本地工作区”，但安全/权限隔离与禁止擅自 `git push` 红线不可突破）。
- **可直接自主执行**：文件审校与编辑、脚本改进、运行本地确定性校验、修复死链与格式瑕疵并复验。
- **需先与用户确认的例外**：变更任一专项的 `Mode R` / `Mode W-Doc` 权限边界、拆并顶层 Skill 包结构、引入外部运行时依赖、执行 `git push` 或破坏性 Git 操作（未明确要求提交时，修改完成后默认停留在本地工作区供审阅 `diff`）。
- **Git 提交记录规范**：一次提交解决一个可独立验证的问题。首行用 `类型: 中文摘要` 写明范围和变化，类型为 `feat`、`fix`、`refactor`、`style`、`chore`、`docs` 或 `test`。空一行后用 `- ` 列出中文要点（通常 1–3 条），可保留技术标识。
- **通用下游校验闭环**：完成修改后运行通用只读校验脚本，确认 `ok: true`、`error_count: 0`、`warning_count: 0`；若有报错或警告须当场修复并复跑至全绿。如因环境/Hook 限制无法执行终端命令，须如实报告阻塞原因并说明静态核验范围，不假定通过：
  ```bash
  python3 -B skills/repo-audit/scripts/verify_governance.py --root . --json
  ```
- **元仓库发布自检项**（独立于通用下游校验，发版或调整技能包结构时额外逐项核验）：
  - [SKILL.md](skills/repo-audit/SKILL.md) 控制在 250 行以内（通用脚本默认按 `<500` 行告警），且 11 份 `skills/repo-audit/references/*.md` 全部由 `SKILL.md` 1 跳直达并与 [README.md](README.md) 覆盖矩阵及调用示例逐一对齐；
  - `skills/repo-audit/` 内零引用、零依赖本仓根目录 `AGENTS.md` / `README.md`；
  - [openai.yaml](skills/repo-audit/agents/openai.yaml) 显式声明有效 `interface.display_name`、`interface.short_description`（25～64 字符）、`interface.default_prompt`（含 `$repo-audit`）与布尔型 `policy.allow_implicit_invocation`；
  - `skills/repo-audit/LICENSE` 存在且与根目录 [LICENSE](LICENSE) 逐字节一致。
