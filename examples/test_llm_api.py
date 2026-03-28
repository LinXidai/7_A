"""自动检测 Responses / Chat Completions 两种接口并流式调用。"""

import asyncio
import os
from pathlib import Path
from typing import Literal

import httpx
from dotenv import load_dotenv
from openai import (
    APIConnectionError,
    AsyncOpenAI,
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    OpenAIError,
    RateLimitError,
)


ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env")

ApiMode = Literal["responses", "chat"]


def _probe_payload(mode: ApiMode) -> dict:
    """用于探测接口存在性的最小请求体。"""
    if mode == "responses":
        return {
            "model": "__route_probe__",
            "input": "ping",
            "max_output_tokens": 1,
        }

    return {
        "model": "__route_probe__",
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
        "stream": False,
    }


async def _supports_mode(client: AsyncOpenAI, mode: ApiMode) -> bool:
    """判断当前网关是否支持某种接口。"""
    endpoint = "responses" if mode == "responses" else "chat/completions"
    url = str(client.base_url).rstrip("/") + f"/{endpoint}"
    headers = {
        "Authorization": f"Bearer {client.api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as http_client:
            response = await http_client.post(url, headers=headers, json=_probe_payload(mode))
    except httpx.HTTPError:
        return False

    return response.status_code not in {404, 405}


async def _detect_api_mode(client: AsyncOpenAI) -> ApiMode:
    """自动检测应使用 responses 还是 chat.completions。"""
    configured = os.getenv("OPENAI_API_MODE", "auto").strip().lower()
    if configured in {"responses", "chat"}:
        return configured  # type: ignore[return-value]

    responses_ok, chat_ok = await asyncio.gather(
        _supports_mode(client, "responses"),
        _supports_mode(client, "chat"),
    )

    if responses_ok:
        return "responses"
    if chat_ok:
        return "chat"

    raise RuntimeError("未检测到可用接口：responses 与 chat/completions 均不可用。")


async def _stream_by_responses(
    client: AsyncOpenAI,
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
) -> None:
    """走 Responses API。"""
    async with client.responses.stream(
        model=model,
        instructions=system_prompt,
        input=user_prompt,
    ) as stream:
        async for event in stream:
            if event.type == "response.output_text.delta":
                print(event.delta, end="", flush=True)

        await stream.get_final_response()


async def _stream_by_chat(
    client: AsyncOpenAI,
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
) -> None:
    """走 Chat Completions API。"""
    stream = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        stream=True,
        temperature=0.7,
    )

    async for chunk in stream:
        content = chunk.choices[0].delta.content
        if content:
            print(content, end="", flush=True)


async def test_llm_stream() -> None:
    """自动检测并测试 LLM 异步流式输出。"""
    client = AsyncOpenAI()
    model = os.getenv("OPENAI_MODEL", "gpt-5")
    system_prompt = "你是一个精通命令行的 AI 助手。"
    user_prompt = "请用一句话解释什么是多 Agent 系统？"
    api_mode = await _detect_api_mode(client)

    print(f"🤖 正在思考... (mode={api_mode}, model={model})\n" + "-" * 40)

    try:
        if api_mode == "responses":
            await _stream_by_responses(
                client,
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
        else:
            await _stream_by_chat(
                client,
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )

        print("\n\n" + "-" * 40 + "\n✅ 输出完成！")

    except AuthenticationError:
        print("\n❌ 鉴权失败：请检查 .env 中的 API Key 是否正确或已过期。")
    except RateLimitError:
        print("\n❌ 速率限制：请求太频繁，或者额度已耗尽。")
    except APIConnectionError:
        print("\n❌ 网络错误：无法连接到 API 服务器，请检查网络或 BASE_URL。")
    except NotFoundError:
        print("\n❌ 接口不存在：当前网关与自动检测结果不匹配。")
    except BadRequestError as exc:
        print(f"\n❌ 请求参数错误：{exc}")
        print("   请重点检查模型名是否受当前网关支持，例如 OPENAI_MODEL=gpt-5。")
    except OpenAIError as exc:
        print(f"\n❌ LLM 服务端发生错误：{exc}")
    except Exception as exc:
        print(f"\n⚠️ 发生未知错误：{exc}")


if __name__ == "__main__":
    asyncio.run(test_llm_stream())
