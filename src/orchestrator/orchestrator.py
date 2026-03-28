"""
Task 2.3 - 总控 Agent（Orchestrator）
功能：双模式输入、上下文感知、意图分类与 Agent 分发
"""

import os
import sys
import asyncio
from dotenv import load_dotenv
from openai import OpenAI
from intent_classifier import (
    classify_intent, handle_intent, stream_direct_answer,
    get_advanced_context, get_system_prompt,
    parse_llm_json, validate_intent_result, _make_fallback,
    CONFIDENCE_HIGH, CONFIDENCE_LOW, DEFAULT_MODEL
)

# === 初始化 ===
load_dotenv()
try:
    client = OpenAI()
except Exception as e:
    print(f"初始化失败: {e}")
    sys.exit(1)


# === 1. 异步命令执行（/ 开头的直接命令）===
async def execute_command_async(command: str):
    """异步执行 shell 命令，流式输出 stdout 和 stderr"""
    print(f"\n[直接执行] $ {command}")
    print("-" * 40)

    try:
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        # 并发读取 stdout 和 stderr
        async def read_stream(stream, prefix=""):
            while True:
                line = await stream.readline()
                if not line:
                    break
                print(f"{prefix}{line.decode().rstrip()}")

        await asyncio.gather(
            read_stream(process.stdout),
            read_stream(process.stderr, prefix="[stderr] ")
        )

        await process.wait()
        print(f"-" * 40)
        print(f"[完成] 退出码: {process.returncode}")

    except Exception as e:
        print(f"[执行错误] {e}")


# === 2. 总控主循环 ===
async def orchestrator_loop():
    """
    总控 Agent 主循环：
    - / 开头 → 直接执行命令（异步流式）
    - 其他输入 → LLM 意图分类 → Agent 分发（复用 handle_intent）
    - 输入 exit/quit 退出
    """
    print("=" * 60)
    print("  Task 2.3 - 总控 Agent (Orchestrator)")
    print("  输入自然语言指令，或用 / 开头直接执行命令")
    print("  输入 exit 或 quit 退出")
    print("=" * 60)

    # 展示当前环境上下文
    ctx = get_advanced_context()
    print(f"\n[环境上下文]")
    print(f"  操作系统: {ctx['os']}")
    print(f"  Shell: {ctx['shell']}")
    print(f"  工作目录: {ctx['pwd']}")
    print(f"  文件数量: {len(ctx['files'].splitlines())} 项")
    print(f"  Git 状态: {ctx['git_status'][:50]}")
    print()

    while True:
        try:
            user_input = input(">>> ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n再见！")
            break

        if not user_input:
            continue

        if user_input.lower() in ("exit", "quit"):
            print("再见！")
            break

        # 双模式判断
        if user_input.startswith("/"):
            # 模式 1：直接命令执行（去掉开头的 /）
            command = user_input[1:].strip()
            if command:
                await execute_command_async(command)
            else:
                print("[提示] / 后面请输入要执行的命令")
        else:
            # 模式 2：自然语言 → LLM 意图分类 → 分发
            handle_intent(user_input, client)


# === 3. 无上下文模式（用于报告对比）===
def classify_without_context(user_input: str):
    """
    不注入环境上下文的意图分类，用于和有上下文的结果做对比。
    System Prompt 中不包含 OS、pwd、文件列表、Git 状态等信息。
    """
    bare_prompt = """你是一个多 Agent 命令行系统的"总控 Agent"。你的唯一任务是分析用户的自然语言输入，判断其意图，并输出结构化的 JSON 结果。

支持的 intent：
1. "shell_agent": 执行系统命令
2. "tool_agent": 调用外部工具
3. "direct_answer": 闲聊或知识问答
4. "clarification": 指令模糊，需要追问

你必须且只能输出一个合法的 JSON 对象：
{
    "intent": "上述四种之一",
    "reasoning": "判断理由",
    "confidence": 0.95,
    "params": {
        "task_description": "任务描述",
        "suggested_tools": [],
        "question": "",
        "options": []
    },
    "fallback_response": ""
}"""

    messages = [
        {"role": "system", "content": bare_prompt},
        {"role": "user", "content": user_input}
    ]

    try:
        response = client.chat.completions.create(
            model=DEFAULT_MODEL,
            messages=messages,
            temperature=0.1
        )
        raw_text = response.choices[0].message.content
        parsed = parse_llm_json(raw_text)
        if parsed:
            valid, _ = validate_intent_result(parsed)
            if valid:
                return parsed
        return _make_fallback(user_input, raw_text)
    except Exception as e:
        return _make_fallback(user_input, str(e))


def compare_context_effect(user_input: str):
    """对比有/无上下文注入时 LLM 输出的差异"""
    print(f"\n{'=' * 60}")
    print(f"  对比测试: \"{user_input}\"")
    print(f"{'=' * 60}")

    # 无上下文
    print(f"\n--- 无上下文注入 ---")
    result_bare = classify_without_context(user_input)
    print(f"  intent: {result_bare.get('intent')}")
    print(f"  confidence: {result_bare.get('confidence')}")
    print(f"  reasoning: {result_bare.get('reasoning', 'N/A')[:120]}")
    print(f"  task_description: {result_bare['params'].get('task_description', 'N/A')}")

    # 有上下文
    print(f"\n--- 有上下文注入 ---")
    result_ctx = classify_intent(user_input, client)
    print(f"  intent: {result_ctx.get('intent')}")
    print(f"  confidence: {result_ctx.get('confidence')}")
    print(f"  reasoning: {result_ctx.get('reasoning', 'N/A')[:120]}")
    print(f"  task_description: {result_ctx['params'].get('task_description', 'N/A')}")

    print(f"\n{'=' * 60}\n")
    return result_bare, result_ctx


# === 主入口 ===
if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--compare":
        # 对比模式：python orchestrator.py --compare
        # 用 3 条不同类型的输入对比有/无上下文的差异
        compare_cases = [
            "帮我看看这个项目有哪些 Python 文件",
            "帮我删除那个文件",
            "这个项目用了什么框架",
        ]
        for case in compare_cases:
            compare_context_effect(case)
    else:
        # 正常交互模式
        asyncio.run(orchestrator_loop())
