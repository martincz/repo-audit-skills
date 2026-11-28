# Repository Guidelines (`repo-audit-skills`)

## 1. 元仓库角色声明（防止指令串扰）

本仓库的交付产物是符合 `agentskills.io` 开放规范的 Agent Skill 包（位于 `skills/repo-audit/`）。
- **技能包与元仓库隔离**：`skills/repo-audit/` 是可独立分发安装到**下游目标仓库**的交付包，其 `SKILL.md` 与 `references/*.md` 不得引用或依赖本元仓库根目录的 `AGENTS.md` / `README.md`；修改技能包时与元仓库自身文档分项隔离，避免将本仓维护规范与下游审计规程混为一谈。
- `skills/repo-audit/` 内的文档是面向**下游目标仓库**执行的审计与治理指令；当你在本仓库内开发维护时，你的职责是**编写与演进这些 Skill 规程及校验脚本**，不受子文档中“目标仓只读零修改”条款对本仓自身的限制（除非用户明确要求对本仓运行自测审计）。
- 按需读取：修改具体专项时仅读取 [SKILL.md](skills/repo-audit/SKILL.md) 与对应 `skills/repo-audit/references/*.md`，无需预先通读全部参考文件。

## 2. 核心架构与合规红线（不可破坏的不变量）

1. **权限门禁物理隔离（`Mode R` vs `Mode W-Doc`）**：
   - `Mode R`（只读审计与可执行蓝图专项）必须保持 100% 零文件修改、零写库副作用，绝不混入直接改业务源码、改测试或改样式的指令；
   - `Mode W-Doc`（规范文档治理专项）仅允许修改列明的目标规范/Skill 文档，不在同一份 `references/*.md` 中混写冲突的读写规则（防止下游推理模型因权限矛盾空耗推理 Token 或误改用户代码）。
2. **三层渐进式披露与单跳拓扑**：
   - 根入口 [SKILL.md](skills/repo-audit/SKILL.md) 只保留意图路由、双模式门禁与全局共享协议（控制在 250 行以内，硬上限 `<500` 行）；
   - 所有专项规程保持 `SKILL.md → references/*.md` 的 **1 层单跳扁平引用**，禁止跨参考文件链式跳转；
   - 指令中引用内置脚本时，一律基于 `<skill-dir>/scripts/...` 解析路径并提供缺失运行环境时的静态降级路径，不可假设目标项目工作目录在本仓。
3. **跨客户端元数据与 MIT 协议纯净性**：
   - `SKILL.md` 的 YAML frontmatter 仅使用 `agentskills.io` 标准字段（`name`、`description`、`license`、`compatibility`、`metadata`、`allowed-tools`），避免引入导致 Codex 等严格解析器拒载的私有键；
   - OpenAI Codex 专属展示与调用策略统一维护在 [openai.yaml](skills/repo-audit/agents/openai.yaml)（`interface.short_description` 保持 25～64 字符，`default_prompt` 显式包含 `$repo-audit`）；
   - [verify_governance.py](skills/repo-audit/scripts/verify_governance.py) 必须保持**纯 Python 3 标准库、零第三方依赖、无交互阻塞**；
   - 严守 [LICENSE](LICENSE)（MIT）纯净性：外部非 MIT 生态技能（如 `frontend-design`）采取运行时探测协同 + 极简客观规则兜底，绝不直接拷贝第三方受限文本。
4. **提示词工程风格基线**：
   - 面向高自主性推理模型（Codex / Claude / Gemini）编写：常规规则使用清晰直接的肯定句与一致的结构分隔（Markdown 层级或语义 XML 标签），仅在真实安全/只读/防数据丢失红线处使用强约束并附简短原因；
   - 推理深度交给运行时参数与原生隐藏思考块，不要求在正文输出原始逐步推理过程；移除冗余的泛化简短催促与易诱发保守漏报的刚性截断/机械复查话术；
   - 保持静态规则在前、动态输入在后的 Prompt Cache 友好布局，明确闭环验收标准、自主决策边界与不可信数据隔离，不将单一行业特定术语写死为通用默认前提（详见 [agent-skills-framework.md](skills/repo-audit/references/agent-skills-framework.md)）。

## 3. 跨文件同步契约

凡新增、合并、重命名或删除 `skills/repo-audit/references/*.md` 专项文件或调整子命令别名，必须在同一次修改中同步更新：
1. [SKILL.md](skills/repo-audit/SKILL.md) 的 `description` 触发范围（保持 ≤1024 字符）与第 1 节短子命令意图路由表；
2. [README.md](README.md) 的「覆盖矩阵与权限边界」表格与调用示例。

## 4. 自主执行边界、提交规范与完成标准

- **可直接自主执行**：文件审校与编辑、脚本改进、运行本地确定性校验、修复死链与格式瑕疵并复验。
- **需先与用户确认的例外**：变更任一专项的 `Mode R` / `Mode W-Doc` 权限边界、拆并顶层 Skill 包结构、引入外部运行时依赖、执行 `git push` 或破坏性 Git 操作（未明确要求提交时，修改完成后默认停留在本地工作区供审阅 `diff`）。
- **Git 提交记录规范**：一次提交解决一个可独立验证的问题。首行用 `类型: 中文摘要` 写明范围和变化，类型为 `feat`、`fix`、`refactor`、`style`、`chore`、`docs` 或 `test`。空一行后用 `- ` 列出中文要点（通常 1–3 条），可保留技术标识。
- **收尾必跑验证命令**（须确保 `ok: true` 且 `error_count: 0`、`warning_count: 0`）：
  ```bash
  python3 skills/repo-audit/scripts/verify_governance.py --root . --json
  ```
