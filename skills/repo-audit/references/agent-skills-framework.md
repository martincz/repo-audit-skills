# Skills 与多智能体框架仓库专项治理（`Mode W-Doc` · 面向 Skills / Agents 交付仓库）

本规程专用于审计并直接升级**以 Agent Skills、系统提示词集、Subagents 定义或多智能体协作工作流作为核心交付产物的仓库**（或在业务仓库含大规模自定义 Skills 套件需深度标准化时启用）。

## 1. 安全门禁与修改范围

严格遵循 `SKILL.md` 的 `Mode W-Doc` 门禁：
- 动手前运行 `git status` 核查未提交改动风险；
- 允许直接重构当前仓库内的 `SKILL.md`、`references/`、`assets/`、`scripts/`、Agent/Subagent 配置文件、`AGENTS.md` 与 `README.md`；可只读比对宿主环境外部已装 Skills 排查命名或触发碰撞，但**不修改目标仓库目录之外的任何文件**；
- 全部升级并通过确定性校验后停留在本地工作区供人工审阅 `diff`，**不自动执行 `git commit` 或 `git push`**。

---

## 2. 四大核心治理与重构维度

### 1. `agentskills.io` 现行规范与包拓扑标准化
- **可发现目录布局**：单 Skill 仓库或多 Skill 集合仓库应保持标准目录拓扑（多 Skill 推荐统一置于 `skills/<skill-name>/SKILL.md`，确保兼容 `npx skills add <repo>` 自动枚举、`--skill` 指定安装以及 `.agents/skills/` 软链挂载）。
- **Frontmatter 硬性约束**：
  - `name`：必填，≤64 字符，严格匹配 `^[a-z0-9]+(-[a-z0-9]+)*$`（无首尾连字符、无连续 `--`），且**必须与父目录名称完全一致**；
  - `description`：必填，非空且 ≤1024 字符。由于 Agent 启动发现阶段仅加载 `name` 与 `description`（约 30～50 tokens），必须高密度写清**核心职责（What）+ 精确触发场景/关键词（When to use）+ 明确排除场景（When NOT to use）**；
  - **多 Skill 触发碰撞消解**：当同仓存在多个相近领域的 Skills 时，逐对审视 `description` 是否存在模糊重叠（例如“前端开发”与“前端只读审计”未做动作词隔离），通过显式动宾词与排除句切清边界，或将过度零散且同源的微碎 Skills 合并为单一入口 + 按需 `references/`。

### 2. 三层渐进式披露（Progressive Disclosure）深度重构
- **根 `SKILL.md` 减负（`<500` 行预算）**：根文件只保留意图路由表、全局安全门禁、共享执行协议与输出标准；把长篇专项检查表、领域 Schema、API 错误码与扩展示例剥离至 `references/`，静态样板文件剥离至 `assets/`。
- **严守单跳（1-Hop）引用深度与显式条件指令**：
  - 所有参考文件均由 `SKILL.md` 直接指向（`SKILL.md → references/foo.md`），消除 `refA.md → refB.md → refC.md` 多级链式跳转导致的加载遗漏与上下文膨胀；
  - 在 `SKILL.md` 中使用**明确的触发条件句**指引按需读取（如“当目标涉及数据保留策略时读取 `references/database-audit.md`”），替换含糊的“更多细节请翻阅 `references/` 目录”。
- **确定性操作下沉为无交互脚本（`scripts/`）**：
  - 对于元数据校验、JSON/YAML 结构检查、死链扫描等确定性逻辑，优先封装为 `scripts/` 下的无副作用可执行脚本，避免每次靠大模型口算文本格式；
  - 脚本必须遵循 Agent 友好规范：**零交互阻塞**（严禁等待 `stdin` 人工输入）、支持 `--help` 自描述参数、输出机器易读格式（如 `--json`）及可自愈的具体报错定位；调用指令中需指明基于 `<skill-dir>/scripts/...` 解析路径并给出运行环境缺失时的静态降级路径。

### 3. 多客户端生态兼容与扩展配置隔离（Codex / Claude / Gemini）
- **保持 `SKILL.md` Frontmatter 跨端纯净**：`SKILL.md` 顶部 YAML 优先严格限制在 `agentskills.io` 标准字段集（`name`、`description`、`license`、`compatibility`、`metadata`、`allowed-tools`），清理会导致 OpenAI Codex 等严格解析器报校验错误并拒载整包的非标私有键。
- **侧车配置与宿主适配**：
  - 针对 OpenAI Codex / ChatGPT：将 UI 展示信息与隐式触发策略放在 `<skill-dir>/agents/openai.yaml`（核查 `interface.short_description` 处于 25～64 字符推荐区间，`interface.default_prompt` 显式包含 `$<skill-name>`，高风险/纯手动重型流程设置 `policy.allow_implicit_invocation: false`）；
  - 若仓库存在与外部成熟官方 Skills（如 Anthropic `frontend-design`、`chrome-devtools` 等）重叠的通用能力，优先设计**运行时检测协同 + 极简兜底**机制，既避免重复制造轮子，又防止直接拷入非兼容开源协议文本污染本仓 License。

### 4. 多智能体职责隔离与推理模型提示词代际升级
- **Subagent 协作契约优化**：若仓库包含多智能体或 Subagent 编排提示词，须厘清每个角色的**读写权限边界**（探索/审计型锁死只读，修改型明确工作区隔离或独占文件集）、**最小上下文输入契约**（只传必要目标与路径，不把冗余历史整包倾倒给子智能体）与**完成汇报结构**。
- **提示词工程现代化（对齐 Claude & OpenAI 官方最佳实践）**：
  - **结构化分隔**：用清晰的 Markdown 层级或语义 XML 标签（如 `<permission_gate>`、`<output_contract>`）隔离硬约束、流程步骤与示例数据，防止指令串扰；
  - **去高压词噪音**：清理满篇滥用的 `CRITICAL / MUST / 严禁 / 绝不`——仅在真正不可突破的安全/只读/数据红线处保留强约束并附简短原因（Why），常规步骤改用清晰直接的肯定句，防止现代模型过触发或僵化停顿；
  - **消除逻辑冲突与补齐校准示例**：清除互相打架的要求（如“全量逐字深读”与“极速简短输出”并存），植入防过度工程护栏与 1～2 组精炼的「应报 vs 不报 / 正例 vs 反例」校准锚点。

---

## 3. 收尾自动化校验与交付摘要

1. **运行确定性自检**：执行 `python3 <skill-dir>/scripts/verify_governance.py --root <target-repo-root> --json`，确保所有 `SKILL.md` 的命名、描述长度、行数预算、`agents/openai.yaml` 侧车及全仓 Markdown 本地相对链接 **0 Error**（发现问题当场修平再复跑）。
2. **同步仓库门面文档**：更新目标仓库根目录 `README.md`（及必要的 `LICENSE`），确保安装命令、Skill 路由/职责矩阵、读写权限边界与实际目录结构 100% 一致。
3. **输出升级实证摘要**：简明列出 ① 目录与合并/拆分调整清单；② 渐进式披露改造前后的文件行数与重复率收敛情况；③ 跨 Agent 客户端兼容性与提示词冲突修复点；④ 自动化校验脚本实测通过结果。
