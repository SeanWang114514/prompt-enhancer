# WorkBuddy enhance-prompt 机制分析（逆向于已安装客户端）

来源：`C:\software\workbuddy\WorkBuddyAI\resources\app.asar` 与 `app.asar.unpacked/cli/product.json`。
分析对象：聊天输入框「提示词增强」按钮背后的 `enhance-prompt` 子代理。

## 1. 结构（Structure）

| 组成 | 标识 | 说明 |
|------|------|------|
| Agent | `enhance-prompt` | 子代理，`tags: [cli, sub-agent]`，无工具（`tools: []`） |
| 使用模型 | `lite` | 固定用轻量模型，配置可被 lite fallback 机制替换 |
| 系统提示词（product.json） | `enhance-prompt-system-prompt` | 面向「写代码助手」的原始版本，含 ANALYSIS PROCESS / CONSTRAINTS / FORMAT / 2 个 few-shot 示例 |
| 系统提示词（内联，实际调用） | 常量 `Ur` | 与 product.json 中 dev 版内容一致 |
| 用户提示词模板（内联） | 常量 `Wr` | language-first 的新版，含 `{input}` 占位符与中文/英文/混合 3 个示例 |
| RPC 通道 | `llm:enhancePrompt` | renderer → daemon |
| 客户端工厂 | `createDefaultEnhancePromptClient` | `CliPulseClient(endpoint, ...)` |

存在**两代提示词**：
- `Ur` / product.json 版 = **dev 模式**（老版，800 字上限，面向代码助手，含"不要建议技术栈/不要问代码片段/聚焦 WHAT"）。
- `Wr` 内联模板 = **general 模式**（新版，语言一致性提到最高优先级，含 BAD/GOOD 输出示例）。

`handleEnhancePrompt` 实际把 `Ur` 作为 `systemPrompt`，把 `renderUserPrompt(text)`（`Wr` 替换 `{input}`）作为 `userPrompt` —— 即两代提示词叠加使用。

## 2. 参数（Parameters）

### 2.1 输入
入口 `createEnhanceService` 接收 `(text, abortSignal, options)`:
- `text: string` — 原始提示词，必填，空白 → `empty_input`
- `abortSignal?: AbortSignal` — 用户中止；中止抛 `enhance-prompt-aborted-by-user`（`AbortError`）
- `options.sessionId?: string` — 非空时透传给 daemon

请求体（renderer→daemon）：`{ text, sessionId? }`

### 2.2 模型调用参数（daemon→LLM）
`CliPulseClient.runAgent({ systemPrompt: Ur, userPrompt: renderUserPrompt(text), agentName: 'enhance-prompt', model: 'lite' })`
- HTTP `POST {endpoint}/api/v1/llm/completions`
- Header: `Content-Type: application/json` + `stringHeaders()`
- `timeout: 300000`（5 分钟）
- 携带 `abortSignal`

### 2.3 输出
`{ success: boolean, text: string, code?: string, error?: string }`
- 成功：`success:true, text:<增强后的提示词>`
- 失败 code：`empty_input` / `sidecar_unavailable` / `llm_error` / `unknown`
- 中止：`Request aborted`（客户端）；`enhance-prompt-aborted-by-user`（服务层）

## 3. 调用流程（Call Flow）

```
[用户输入 / 点击增强按钮]
        │
        ▼ (IPC invoke)
a.enhancePrompt = invoke(t, r, 'llm:enhancePrompt')       // renderer 暴露
        │
        ▼
createEnhanceService(text, abortSignal, options):
  1. if abortSignal.aborted → throw AbortError
  2. daemonClient = getWorkbuddyDaemonClient()
     if !daemonClient.enhancePrompt → Error
  3. payload = { text }; 若 sessionId 非空则加入
  4. s = daemonClient.enhancePrompt(payload)
  5. s = raceWithAbortSignal(s, abortSignal)             // 可中止
  6. await s → c = { success, text, code?, error? }
  7. if c.success → 触发 lite fallback toast 检查（异步、忽略错误）
  8. if !c.success → throw EnhancePromptBusinessError(c.error, c.code)
  9. if c.text.trim()==='' → throw EnhancePromptBusinessError('Enhanced result is empty','llm_error')
 10. return c.text
        │
        ▼ (daemon)
registerEnhancePromptHandlers: e.handle('llm:enhancePrompt', ...)
  → handleEnhancePrompt(deps, { text }):
     a. text 空白 → { success:false, code:'empty_input' }
     b. getSidecarEndpoint() 失败/为空 → { success:false, code:'sidecar_unavailable' }
     c. createClient(endpoint).runAgent({
          systemPrompt: Ur,
          userPrompt: Wr.replace('{input}', text),
          agentName: 'enhance-prompt',
          model: 'lite'
        })
     d. 抛异常 → { success:false, code:'unknown' }
     e. !result.success → { success:false, code:'llm_error' }
     f. text = stripWrappingQuotes(result.text)
        // .trim() 后正则去掉首尾 " ' “ ” ‘ ’
     g. 空 → { success:false, code:'llm_error', error:'Enhanced result is empty' }
     h. return { success:true, text }
        │
        ▼
CliPulseClient → POST /api/v1/llm/completions (lite, timeout 300s)
        │
        ▼
[增强后的提示词回填到输入框]
```

## 4. 核心机制要点

1. **只改写，不回答**：系统提示词明确 "Do NOT answer questions - expand/rewrite them"。
2. **语言一致性最高优先级**：dev 与 general 两版都把它列为 IMPORTANT/CRITICAL，且 general 版强调不要把语言分析写进输出。
3. **简洁**：≤ 800 字符；避免悬空列表/连词/冒号。
4. **保真**：保持原目标、主题、约束、目标输出类型；只做澄清、补上下文、结构化、去冗余。
5. **不做的事**：不主动要 how-to、不问代码片段、不擅自推荐技术栈、不解释 HOW、不加无关需求/无依据事实。
6. **健壮性**：输入校验、endpoint 解析失败、LLM 失败、空结果四层错误码；`stripWrappingQuotes` 清理引号；AbortSignal 竞态中止；lite 模型 fallback 提示。

## 5. 重构映射（Original → Skill）

| 原始能力 | 重构实现 | 覆盖 |
|----------|----------|------|
| `enhance-prompt` agent + `lite` 模型 | SKILL.md 工作流 + `model='lite'` 默认 | ✅ |
| `enhance-prompt-system-prompt`（dev 版） | `references/prompt_templates.md` DEV_TEMPLATE，`--mode dev` | ✅ |
| 内联 `Ur` | 与 DEV_TEMPLATE 同源 | ✅ |
| `Wr` 用户模板 + `{input}` | `references/prompt_templates.md` GENERAL_TEMPLATE + 脚本替换 | ✅ |
| 语言一致性最高优先级 | Enhancement Rules #4 + general 模板 | ✅ |
| ≤800 字约束 | Enhancement Rules #4 + 脚本 `--max-chars` | ✅ |
| 只改写不回答 / 不 how-to / 不代码片段 / 不推荐技术 | Enhancement Rules #4 + 两模板 | ✅ |
| `stripWrappingQuotes` | 脚本 `strip_wrapping_quotes()` | ✅ |
| 空输入 / 无 endpoint / LLM 失败 / 空结果 四层错误码 | 脚本 I/O 契约 `code` 字段 | ✅ |
| `raceWithAbortSignal` 中止 | 脚本超时 + `AbortSignal`（可选） | 部分（脚本级超时；原生 agent 执行可中断） |
| lite fallback toast | 未复刻（属 UI 提示，非增强能力） | 不适用 |
| `POST /api/v1/llm/completions` | 脚本走可配置 `WB_LLM_ENDPOINT`（OpenAI 兼容） | ✅（接口形态可配置） |

结论：重构后的 skill 在**提示词改写能力、语言/长度/保真约束、错误契约、两代模板**上完整覆盖原实现；仅省略纯 UI 的 lite fallback toast，并可将中止/后端形态按宿主环境配置。
