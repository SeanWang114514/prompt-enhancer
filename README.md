# prompt-enhancer

WorkBuddy 内置「提示词增强」（`enhance-prompt` 子代理）能力的逆向重构版，封装为一个可复用 Skill。

把一句简短或模糊的需求，改写成**更清晰、更具体、同语言**的提示词——只改写指令，**不回答问题**。面向支持代码能力的 AI 助手，完整复刻原版的语言一致性、约 800 字简洁度、保真约束与两代提示词模板。

## 能力

- 语言严格一致：中文进中文出，英文进英文出，中英混合保持自然混合，且绝不输出语言标签
- 保真改写：保留原目标、主题、约束和目标输出类型，只做澄清、补上下文、结构化、去冗余
- 边界约束：不主动要 how-to、不问代码片段、不擅自推荐技术栈、不解释 HOW、聚焦 WHAT
- 后处理：裁剪首尾引号、空结果检测
- 四种错误契约：`empty_input` / `sidecar_unavailable` / `llm_error` / `unknown`
- 三种模式：`original`（出厂精确复刻）/ `dev`（经典代码助手版）/ `general`（语言优先版）

## 安装

```bash
# 用户级（所有项目可用）
unzip prompt-enhancer.zip -d ~/.workbuddy/skills/

# 项目级（随仓库共享）
unzip prompt-enhancer.zip -d <project>/.workbuddy/skills/

# 结果应为 ~/.workbuddy/skills/prompt-enhancer/SKILL.md
```

也可直接把本仓库整个内容作为 skill 目录使用。

## 使用

### 方式一：作为 Skill 触发

安装后，在其他 Agent 中用 Skill 工具调用，或直接说出触发语：

> 增强提示词：帮我解释这段代码的功能

### 方式二：脚本直接运行

```bash
# 只组装请求（无需网络/LLM），打印 systemPrompt + userPrompt + agentName + model
python scripts/enhance_prompt.py --dry-run --text "帮我解释这段代码的功能"

# 经典代码助手模式
python scripts/enhance_prompt.py --mode dev --text "a website for my dog"

# 从 stdin 读输入
echo "Convert this to a friendly tone, use canvas" | python scripts/enhance_prompt.py --dry-run

# 接真实后端（OpenAI 兼容 chat/completions）
WB_LLM_ENDPOINT=https://api.example.com/v1/chat/completions \
WB_LLM_KEY=TOKEN \
WB_LLM_MODEL=lite \
python scripts/enhance_prompt.py --text "explain this code"
```

未配置 `WB_LLM_ENDPOINT` 时脚本返回 `sidecar_unavailable`，不会编造结果；此时可由 Agent 自身按提示词规则执行增强。

### 输出格式

```json
{ "success": true, "text": "请解释这段代码的主要功能、执行流程和关键逻辑。", "code": null, "error": null }
```

失败时 `success=false`，`code` 为上述四种错误码之一，`error` 给出细节。

## 目录结构

```
prompt-enhancer/
├── SKILL.md                        # 触发条件 / I/O 契约 / 增强规则 / 工作流 / 集成
├── README.md                       # 本文件
├── references/
│   ├── mechanism.md                # 逆向分析：结构、参数、调用流程、重构映射
│   └── prompt_templates.md         # 两代提示词逐字模板 + 后处理函数
└── scripts/
    └── enhance_prompt.py           # 可运行复刻：校验 / 组装 / LLM 调用 / 后处理
```

## 逆向来源与机制

分析自已安装 WorkBuddy 客户端的 `app.asar` 与 `app.asar.unpacked/cli/product.json`：

- Agent：`enhance-prompt`，模型 `lite`，无工具，tags `[cli, sub-agent]`
- 两代提示词：`enhance-prompt-system-prompt`（dev 版，≤800 字）+ 内联 `Wr` 模板（language-first 版）
- 实际调用：`systemPrompt=Ur` + `userPrompt=Wr.replace('{input}', text)`
- 调用链：`llm:enhancePrompt`(IPC) → `createEnhanceService` → daemon `handleEnhancePrompt` → `POST /api/v1/llm/completions` → `stripWrappingQuotes` → 回填输入框

完整细节与逐项覆盖对照见 [`references/mechanism.md`](references/mechanism.md)。

## 验证

- `quick_validate.py` / `package_skill.py`：`Skill is valid`
- `py_compile`：通过
- dry-run（3 种模式）、空输入、stdin、无 endpoint：均返回预期 JSON
- Mock LLM 全流程：引号剥离与 I/O 契约通过

## 说明

本项目是对已安装客户端中提示词模板与调用结构的逆向重构，用于技能复用研究。`prompt_templates.md` 中的模板逐字来自客户端资源文件。
