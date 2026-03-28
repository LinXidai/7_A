"""后端：直接执行 CLI 命令"""

import asyncio
import errno
import os

from .tui import AgentCLI

if os.name != "nt":
    import pty
    import termios


def _disable_terminal_echo(fd: int) -> None:
    """关闭 PTY 从端回显，避免 UI 与子进程重复显示用户输入"""
    attributes = termios.tcgetattr(fd)
    attributes[3] &= ~termios.ECHO
    termios.tcsetattr(fd, termios.TCSANOW, attributes)


async def _stream_pty_output(master_fd: int, ui: AgentCLI) -> None:
    """异步读取 PTY 输出，确保无换行 prompt 也能及时显示"""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[bytes | BaseException] = asyncio.Queue()

    def on_pty_ready() -> None:
        try:
            data = os.read(master_fd, 4096)
        except OSError as exc:
            if exc.errno == errno.EIO:
                data = b""
            else:
                queue.put_nowait(exc)
                return
        queue.put_nowait(data)

    loop.add_reader(master_fd, on_pty_ready)
    try:
        while True:
            item = await queue.get()
            if isinstance(item, BaseException):
                raise item
            if not item:
                break

            decoded = item.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
            decoded = decoded.rstrip("\n")
            if decoded:
                ui.output_system(decoded)
    finally:
        loop.remove_reader(master_fd)


async def _execute_shell_stream_pty(command: str, ui: AgentCLI) -> None:
    """POSIX 下使用 PTY 执行交互式命令"""
    master_fd: int | None = None
    slave_fd: int | None = None

    try:
        master_fd, slave_fd = pty.openpty()
        _disable_terminal_echo(slave_fd)

        process = await asyncio.create_subprocess_shell(
            command,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            start_new_session=True,
        )
        os.close(slave_fd)
        slave_fd = None

        ui.current_process = process
        ui.current_process_group_id = process.pid
        ui.current_process_input_fd = master_fd
        ui.current_process_terminated_by_user = False

        await _stream_pty_output(master_fd, ui)

        return_code = await process.wait()
        if ui.current_process_terminated_by_user:
            ui.output_system("已终止当前命令进程组", style="bold yellow")
        elif return_code != 0:
            ui.output_system(f"ERROR: 命令执行失败 (Return Code: {return_code})")

    except Exception as exc:
        ui.output_system(f"ERROR: 进程启动异常: {exc}")
    finally:
        if slave_fd is not None:
            try:
                os.close(slave_fd)
            except OSError:
                pass
        if master_fd is not None:
            try:
                os.close(master_fd)
            except OSError:
                pass

        ui.current_process = None
        ui.current_process_group_id = None
        ui.current_process_input_fd = None
        ui.current_process_terminated_by_user = False


async def _execute_shell_stream_pipe(command: str, ui: AgentCLI) -> None:
    """非 POSIX 环境下退回 PIPE 模式"""

    try:
        process = await asyncio.create_subprocess_shell(
            command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=(os.name != "nt"),
        )

        # 子进程传给 UI 前端
        ui.current_process = process
        ui.current_process_group_id = process.pid if os.name != "nt" else None
        ui.current_process_input_fd = None
        ui.current_process_terminated_by_user = False

        async def read_stream(stream, is_error: bool = False) -> None:
            """逐行读取流数据，并非阻塞推送给 UI"""
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
        if ui.current_process_terminated_by_user:
            ui.output_system("已终止当前命令进程组", style="bold yellow")
        elif return_code != 0:
            ui.output_system(f"ERROR: 命令执行失败 (Return Code: {return_code})")

    except Exception as exc:
        ui.output_system(f"ERROR: 进程启动异常: {exc}")
    finally:
        ui.current_process = None
        ui.current_process_group_id = None
        ui.current_process_input_fd = None
        ui.current_process_terminated_by_user = False


async def execute_shell_stream(command: str, ui: AgentCLI) -> None:
    """异步执行系统命令，并流式将输出推送到前端"""
    if os.name != "nt":
        await _execute_shell_stream_pty(command, ui)
    else:
        await _execute_shell_stream_pipe(command, ui)


async def main_controller(user_input: str, ui: AgentCLI) -> None:
    """根据用户输入分发给 Shell 或 LLM"""
    if user_input.startswith("/"):
        command = user_input[1:].strip()
        await execute_shell_stream(command, ui)
        return

    mock_llm_response = """
分析完毕这是一个需要用到 Python 的任务，您可以参考以下代码：
```python
def example():
    print("Agent thinking...")
    # 发生 error 时会触发你自定义的红色高亮
```
"""
    ui.output_llm(mock_llm_response)
