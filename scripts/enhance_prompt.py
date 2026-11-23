#!/usr/bin/env python3
"""
enhance_prompt.py - faithful, self-contained reimplementation of WorkBuddy's
`enhance-prompt` sub-agent (chat input-box "提示词增强" button).

Modes (--mode):
  original    Exact shipped behavior: system=DEV template (Ur), user=GENERAL
              template (Wr) with {input} substituted. DEFAULT.
  dev         Classic dev-assistant behavior: system=DEV template, user=raw text.
  general     Language-first behavior: system=short instruction, user=GENERAL
              template with {input} substituted.

Other flags:
  --dry-run   Assemble the exact request payload (system + user prompt, agent,
              model) and print it as JSON. No network, no LLM required. Use this
              when acting as the LLM yourself: load the printed system/user
              prompts, apply the Enhancement Rules, emit only the enhanced text.
  (no dry-run) Call an OpenAI-compatible chat-completions endpoint configured via
              env vars, then post-process and return the I/O-contract object.

Env vars for the live backend:
  WB_LLM_ENDPOINT   full chat-completions URL, e.g. https://api.example.com/v1/chat/completions
  WB_LLM_KEY        bearer token (optional if endpoint is open)
  WB_LLM_MODEL      model name (default: lite)

I/O contract (stdout JSON):
  { "success": bool, "text": str, "code": str|null, "error": str|null }
  codes: empty_input | sidecar_unavailable | llm_error | unknown
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REFS = os.path.join(HERE, "..", "references", "prompt_templates.md")
DEV_TEMPLATE = """You are a Prompt Engineering Expert specializing in improving user prompts for a development code assistant. When given a prompt, analyze and enhance it to create a more effective version while maintaining its core purpose. The requests are being made to an AI assistant that specializes in writing code.

TASK: When given a prompt, analyze and enhance it to create a more effective version while maintaining its core purpose. The requests are being made to an AI assistant that specializes in writing code.

ANALYSIS PROCESS:
Evaluate the original prompt: identify the main objective, note ambiguities or gaps, assess clarity, check for missing context.
Apply these principles: write clear specific instructions; include necessary context; set explicit parameters and constraints; structure the output format; add relevant examples; match tone and complexity to the use case; remove redundant information.
Create the enhanced version: maintain the original goal; incorporate improvements; ensure clarity and completeness; be realistic in features.
Do NOT request guides/how-tos unless the user asks. Do NOT ask for code snippets. Do NOT suggest specific technologies unless mentioned. Do NOT explain HOW - focus on WHAT. Do NOT answer questions - expand/rewrite them.
IMPORTANT CONSTRAINTS:
1. Language matching is the highest priority - respond in the exact same language as the user's input; never mix languages unless the input does.
2. Keep the enhanced prompt concise - around {max_chars} characters maximum.
FORMAT: Provide only the enhanced prompt with no additional commentary."""

GENERAL_TEMPLATE = """You are a prompt enhancement assistant. Improve the user prompt while preserving its intent and language.

USER INPUT:
{input}

TASK:
Rewrite the user input into a clearer, more specific prompt for the target AI assistant.

CRITICAL PRIORITY - LANGUAGE CONSISTENCY:
1. Detect the language of the user input and write the enhanced prompt in that same language.
2. Chinese input -> entirely Chinese output. English input -> entirely English output.
3. Any other language -> that exact same language.
4. Mixed input -> keep a natural matching mix; do not translate into a single language.
5. These are behavior instructions only; never include language analysis or labels in the output.

ENHANCEMENT REQUIREMENTS:
1. Return only the enhanced prompt text; no explanations, prefaces, markdown fences, labels, or analysis.
2. No language labels or meta notes.
3. Preserve original intent, topic, constraints, and target output type. Do not answer the request.
4. Make a substantive enhancement when possible: clarify task, scope, constraints, expected output.
5. If already clear, lightly polish instead of returning unchanged.
6. Keep it complete and concise; never end on an unfinished list, dangling conjunction, or trailing colon.
7. Add no unrelated requirements, unsupported facts, or unnecessary sections.

EXAMPLES:
User input (Chinese): "请帮我解释这段代码的功能"
Enhanced: "请解释这段代码的主要功能、执行流程和关键逻辑，并指出可能需要注意的边界情况。"
User input (English): "Please explain what this code does"
Enhanced: "Explain what this code does, including its main purpose, key control flow, and any important edge cases."
User input (Mixed): "这段代码有 bug，can you help me fix it?"
Enhanced: "请分析这段代码中的 bug，explain the root cause, and provide a minimal fix with necessary verification steps."
BAD: "User input is in Chinese -> Response must be in Chinese. 请解释这段代码的主要功能"
GOOD: "请解释这段代码的主要功能、执行流程和关键逻辑，并指出可能需要注意的边界情况。"
"""

QUOTE_RE = re.compile(r'^["\'“”‘’]|["\'“”‘’]$')


def result(success: bool, text: str = "", code: str | None = None,
           error: str | None = None) -> dict:
    return {"success": success, "text": text, "code": code, "error": error}


def strip_wrapping_quotes(text: str) -> str:
    """Equivalent to the shipped JS stripWrappingQuotes()."""
    return QUOTE_RE.sub("", text.strip())


def build_payload(text: str, mode: str, max_chars: int, session_id: str | None) -> dict:
    """Assemble the exact original-style runAgent payload."""
    if mode == "dev":
        system_prompt = DEV_TEMPLATE.format(max_chars=max_chars)
        user_prompt = text  # classic dev mode: raw text is the user turn
    elif mode == "general":
        # language-first template carries the rules; short system instruction
        # keeps the runAgent system/user shape.
        system_prompt = ("You are a prompt enhancement assistant. Improve the user "
                         "prompt while preserving its intent and language. Return "
                         "only the enhanced prompt.")
        user_prompt = GENERAL_TEMPLATE.replace("{input}", text)
    else:  # original: exact shipped combination — DEV as system + Wr(text) as user
        system_prompt = DEV_TEMPLATE.format(max_chars=max_chars)
        user_prompt = GENERAL_TEMPLATE.replace("{input}", text)
    payload = {
        "systemPrompt": system_prompt,
        "userPrompt": user_prompt,
        "agentName": "enhance-prompt",
        "model": os.environ.get("WB_LLM_MODEL", "lite"),
    }
    if session_id and session_id.strip():
        payload["sessionId"] = session_id
    return payload


def call_llm(payload: dict, timeout: int) -> str:
    endpoint = os.environ.get("WB_LLM_ENDPOINT", "").strip()
    if not endpoint:
        raise RuntimeError("sidecar_unavailable")
    key = os.environ.get("WB_LLM_KEY", "").strip()
    body = json.dumps({
        "model": payload["model"],
        "messages": [
            {"role": "system", "content": payload["systemPrompt"]},
            {"role": "user", "content": payload["userPrompt"]},
        ],
        "stream": False,
    }).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # network / http / parse failures
        raise RuntimeError(f"llm_error: {exc}") from exc
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"llm_error: malformed response: {exc}") from exc


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--text", help="raw prompt; if omitted, read stdin")
    ap.add_argument("--mode", choices=["original", "dev", "general"], default="original")
    ap.add_argument("--max-chars", type=int, default=800)
    ap.add_argument("--session-id", default=None)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--dry-run", action="store_true",
                    help="print assembled payload as JSON, no network call")
    args = ap.parse_args()

    text = args.text if args.text is not None else sys.stdin.read()
    if not text.strip():
        print(json.dumps(result(False, code="empty_input", error="Empty input"),
                         ensure_ascii=False))
        return 0

    payload = build_payload(text, args.mode, args.max_chars, args.session_id)

    if args.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    try:
        raw = call_llm(payload, args.timeout)
    except RuntimeError as exc:
        msg = str(exc)
        code = "sidecar_unavailable" if "sidecar_unavailable" in msg else "llm_error"
        print(json.dumps(result(False, code=code, error=msg), ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps(result(False, code="unknown", error=str(exc)), ensure_ascii=False))
        return 0

    cleaned = strip_wrapping_quotes(raw)
    if not cleaned:
        print(json.dumps(result(False, code="llm_error",
                                error="Enhanced result is empty"), ensure_ascii=False))
        return 0
    print(json.dumps(result(True, text=cleaned), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
