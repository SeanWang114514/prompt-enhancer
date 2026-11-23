---
name: prompt-enhancer
description: Reconstructed WorkBuddy "enhance-prompt" capability. Refines a short or vague user instruction into a clearer, more specific, same-language prompt (targeting a code-capable AI assistant) without answering it. Use this skill when the user asks to 增强/优化/改写/润色提示词, "enhance this prompt", wants a vague ask expanded before execution, or when reproducing the chat-input-box "提示词增强" button behavior. Handles language matching, ~800-char concision, quote stripping, and the two prompt variants (dev-assistant + language-first general), with an original mode that reproduces the exact shipped combination.
agent_created: true
---

# Prompt Enhancer (增强提示词 / enhance-prompt 重构版)

## Overview

This skill reconstructs WorkBuddy's built-in `enhance-prompt` sub-agent as a reusable, self-contained module. It turns a raw, often short instruction into a **more effective prompt while preserving the original goal** — it rewrites/expands the request, never answers it. Language of the output must exactly match the language of the input.

The reconstruction is faithful to the shipped implementation (see `references/mechanism.md` for the extracted structure, parameters, and call flow). Two prompt variants are preserved:

- **dev mode** (`enhance-prompt-system-prompt`): the original, tuned for a code-writing assistant; caps output around 800 chars.
- **general mode** (inline `Wr` template): the newer, language-first variant actually used by the daemon handler.

## When To Use

Trigger on any of these:
- 用户说「增强提示词 / 优化这个提示词 / 把这句话改写成更好的 prompt / 润色需求」
- "enhance / refine / improve / rewrite this prompt"
- 一个模糊的需求需要先被改写成清晰指令再执行（例如聊天输入框的增强按钮）
- 需要复现 WorkBuddy 输入框「提示词增强」行为，或需要与其他 skill/agent 集成该能力

Do **not** use when the user wants the actual task done (write the code, explain the concept, etc.) — enhancement only rewrites the instruction.

## I/O Contract

**Input**
| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `text` | string | yes | Raw prompt. Empty/whitespace-only → `empty_input`. |
| `mode` | `original` \| `dev` \| `general` | no | Defaults to `original` (exact shipped behavior: DEV system + GENERAL user template). `dev` = classic dev-assistant prompt only; `general` = language-first prompt only. |
| `session_id` | string | no | Passed through for parity; unused by the transformation itself. |

**Output** (single object)
| Field | Type | Notes |
|-------|------|-------|
| `success` | bool | True only when a non-empty enhanced prompt was produced. |
| `text` | string | The enhanced prompt only — no commentary, labels, or markdown fences. |
| `code` | string | Error code when `success=false`: `empty_input`, `sidecar_unavailable`, `llm_error`, `unknown`. |
| `error` | string | Human-readable error detail. |

**Failure rules**: empty input → `empty_input`; no usable LLM backend → `sidecar_unavailable`; model call fails or returns empty after cleanup → `llm_error`. Never invent an enhanced result.

## Enhancement Rules (apply in order)

1. **Analyze**: identify main objective; note ambiguities/gaps; assess instruction clarity; check missing context.
2. **Apply principles**: clear & specific instructions; include necessary context; set explicit parameters/constraints; structure output format; add relevant examples; match tone & complexity to use case; remove redundancy.
3. **Compose enhanced version**: maintain original goal; incorporate the improvements; ensure clarity/completeness; add only realistic features.
4. **Hard constraints** (from the original system prompt):
   - **Language matching is highest priority** — output in the exact language of the input (mixed input → natural mix). Never add language labels/meta notes.
   - Keep it concise — roughly ≤ 800 characters (dev mode). Do not end on a dangling list, conjunction, or colon.
   - Do NOT request guides/how-tos unless asked. Do NOT ask for code snippets. Do NOT suggest specific technologies unless the user mentioned them. Do NOT explain HOW — focus on WHAT. Do NOT answer the question — expand/rewrite it.
5. **Post-process**: trim; strip one pair of wrapping quotes (`" ' “ ” ‘ ’`); re-check non-empty.
6. **Format**: output only the enhanced prompt, no extra commentary.

## Workflow

1. Determine `mode`: default `original` (faithful reproduction); `dev` for the pure classic code-assistant prompt; `general` for the pure language-first prompt.
2. Run `scripts/enhance_prompt.py` with `--text` (or pipe text via stdin). The script:
   - validates input and applies `stripWrappingQuotes`,
   - builds the exact request payload (`systemPrompt` + `userPrompt` with `{input}` substituted + `agentName='enhance-prompt'` + `model='lite'`),
   - calls an OpenAI-compatible LLM endpoint if configured, otherwise prints the assembled prompt so the agent can run it natively,
   - applies rules 4–6 and returns the I/O contract object.
3. If no endpoint is configured, act as the LLM yourself: load `references/prompt_templates.md`, follow the Enhancement Rules, and emit only the enhanced prompt.
4. Return the final `text` to the user. Do not reveal the analysis steps.

Example invocations:
```bash
# Assemble the exact request payload (no network needed)
python scripts/enhance_prompt.py --mode general --text "帮我解释这段代码的功能" --dry-run

# Call a configured OpenAI-compatible endpoint (default mode = original)
WB_LLM_ENDPOINT=https://api.example.com/v1/chat/completions WB_LLM_KEY=TOKEN \
  python scripts/enhance_prompt.py --text "a website for my dog"

# Pipe input
echo "Convert this to a friendly tone, use canvas" | python scripts/enhance_prompt.py --dry-run
```

## Integration

- **Install location**: user skill at `~/.workbuddy/skills/prompt-enhancer/` (available in all projects). Invoke via the `Skill` tool or by name; other skills/agents can call the script directly.
- **Backend**: set `WB_LLM_ENDPOINT` (OpenAI-compatible chat-completions URL), `WB_LLM_KEY`, optional `WB_LLM_MODEL` (default `lite`). With no endpoint the skill degrades to native agent execution — it never fabricates output.
- **Parity with original**: `agentName='enhance-prompt'`, `model='lite'`, system prompt = dev or general template, user prompt = template with `{input}` = raw text, post-process = `stripWrappingQuotes`. See `references/mechanism.md` for the mapping table.

## Resources

- `references/mechanism.md` — extracted original structure, parameters, call flow, I/O contract, and reconstruction mapping.
- `references/prompt_templates.md` — the two prompt templates verbatim (dev + general), usable directly as system/user prompts.
- `scripts/enhance_prompt.py` — deterministic reimplementation: validation, payload assembly, optional LLM call, post-processing, I/O-contract output.
