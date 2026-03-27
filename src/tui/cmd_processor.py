"""后端：CLI 命令直接执行 / 普通输入走 LLM 处理。"""

import asyncio

from .ui import AgentCLI


async def execute_shell_stream(command: str, ui: AgentCLI) -> None:
    """异步执行系统命令，并流式将输出推送到前端。"""
    ui.output_system(f"⚙️ 正在执行系统命令: {command}", style="bold yellow")

    try:
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        async def read_stream(stream, is_error: bool = False) -> None:
            """逐行读取流数据，并非阻塞推送给 UI。"""
            style = "bold red" if is_error else "white"
            while True:
                line = await stream.readline()
                if not line:
                    break
                decoded_line = line.decode("utf-8", errors="replace").rstrip()
                ui.output_system(decoded_line, style=style)

        await asyncio.gather(
            read_stream(process.stdout),
            read_stream(process.stderr, is_error=True),
        )

        return_code = await process.wait()
        if return_code == 0:
            ui.output_system(f"✅ 命令执行成功 (Return Code: {return_code})", style="bold green")
        else:
            ui.output_system(f"❌ 命令执行失败 (Return Code: {return_code})", style="bold red")

    except Exception as exc:
        ui.output_system(f"⚠️ 进程启动异常: {exc}", style="bold red")


async def main_controller(user_input: str, ui: AgentCLI) -> None:
    """根据用户输入分发给 Shell 或 LLM。"""
    if user_input.startswith("/"):
        command = user_input[1:].strip()
        await execute_shell_stream(command, ui)
        return

    mock_llm_response = """
分析完毕。这是一个需要用到 Python 的任务，您可以参考以下代码：
```python
def example():
    print("Agent thinking...")
    # 发生 error 时会触发你自定义的红色高亮
```
"""
    ui.output_llm(mock_llm_response)
