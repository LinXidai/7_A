# TUI 前后端对接接口（简版）

这份文档给后端同事用，只保留真正需要对接的部分：

1. **后端如何接收输入**
2. **后端如何把结果输出到前端**
3. **现有 `cmd_processor.py` 如何作为一个已实现后端直接复用**

---

## 1. 你真正要对接的接口

后端只需要实现一个异步函数：

```python
async def command_handler(user_input: str, ui: AgentCLI) -> None:
    ...
```

然后启动前端时传进去：

```python
from src.tui.application import AgentCLI

app = AgentCLI(command_handler=command_handler)
app.run()
```

当前项目默认启动方式就是这个：

```bash
venv/bin/python -m src.tui.main
```

其中 `src/tui/main.py` 默认挂载的是：

```python
from .cmd_processor import main_controller
```

也就是说，**`cmd_processor.py` 已经是一个可运行的后端示例**。

---

## 2. 后端如何“接收输入”

### 输入入口

前端不会让后端直接读输入框；而是会在用户按回车后，把文本作为参数传给：

```python
async def command_handler(user_input: str, ui: AgentCLI) -> None:
```

所以：

- `user_input` = 用户刚提交的那一行文本
- `ui` = 前端实例，后端用它把结果写回界面

### 输入触发流程

以用户在输入框中输入 `你好` 为例：

1. 用户在底部输入框输入：
   ```text
   你好
   ```
2. 用户按回车
3. 前端先把这条用户输入回显到日志区
4. 前端调用：
   ```python
   await command_handler("你好", ui)
   ```
5. 后端开始处理，并通过 `ui.output_xxx(...)` 输出结果

### 一个更具体的例子

如果用户输入：

```text
/ls
```

那么后端收到的是：

```python
user_input == "/ls"
```

如果用户输入：

```text
帮我写一个 hello world
```

那么后端收到的是：

```python
user_input == "帮我写一个 hello world"
```

---

## 3. 特殊情况：子进程运行时，输入不会再进后端

如果后端启动了一个交互式命令（如 Python 脚本、REPL、需要 `input()` 的程序），那么在该命令运行期间，用户后续输入会**直接转发给子进程**，而不是再次进入 `command_handler`。

例如：

1. 用户输入：
   ```text
   /python3 examples/test_stdin.py
   ```
2. 后端执行这个命令后，程序输出：
   ```text
   👉 请输入你的名字:
   ```
3. 这时用户再输入：
   ```text
   Alice
   ```
4. 这条 `Alice` 会直接写入子进程 stdin / PTY
5. **不会再调用** `command_handler("Alice", ui)`

这点对后端很重要：

> **只有在“当前没有交互式子进程运行”时，新输入才会进入后端入口函数。**

---

## 4. 后端如何“输出到前端”

后端统一通过 `ui` 暴露的方法输出，不要直接操作底层控件。

### 4.1 输出系统信息 / 命令输出

```python
ui.output_system(text, style="")
```

用途：

- 系统提示
- 调试日志
- Shell 命令输出
- 错误信息

示例：

```python
ui.output_system("系统初始化完成", style="bold cyan")
ui.output_system("开始执行命令...", style="yellow")
ui.output_system("ERROR: 文件不存在")
```

效果说明：

- 支持 Rich 样式字符串
- 文本里的 `error / ERROR` 会自动标红

---

### 4.2 输出 LLM 回复

```python
ui.output_llm(content, markdown=None, language=None)
```

用途：

- 模型普通文本回复
- Markdown 回复
- 代码高亮回复

示例 1：普通文本

```python
ui.output_llm("任务已分析完毕")
```

示例 2：Markdown

```python
ui.output_llm("## 分析结果\n- 方案 A\n- 方案 B", markdown=True)
```

示例 3：代码高亮

```python
ui.output_llm("print('hello world')", language="python")
```

参数说明：

- `markdown=True`：强制按 Markdown 渲染
- `markdown=False`：按普通文本渲染
- `markdown=None`：自动检测
- `language="python"`：按对应语言做代码高亮

当前前端规则：

- 用户输入：只回显，不渲染 Markdown
- 系统输出：只做 `error` 标红
- LLM 输出：支持 Markdown / 代码高亮

---

### 4.3 `ui.output_user(text)`

这个接口用于输出用户消息：

```python
ui.output_user("hello")
```

但通常后端**不需要主动调用**，因为前端在回车提交时已经自动回显用户输入。

---

## 5. 直接复用现有后端：`cmd_processor.py`

当前仓库里，`src/tui/cmd_processor.py` 已经实现了一版后端逻辑：

- 以 `/` 开头：当作系统命令执行
- 其他输入：当作 LLM 输入处理

核心逻辑等价于：

```python
async def main_controller(user_input: str, ui: AgentCLI) -> None:
    if user_input.startswith("/"):
        command = user_input[1:].strip()
        await execute_shell_stream(command, ui)
        return

    ui.output_llm("...模型回复...")
```

### 你可以如何使用它

#### 案例 1：执行 ls

用户输入：

```text
/ls
```

后端行为：

```python
command = "ls"
await execute_shell_stream("ls", ui)
```

前端表现：

- 日志区先显示用户输入 `> /ls`
- 然后流式显示 `ls` 的输出

---

#### 案例 2：执行 ping 命令

按当前 `cmd_processor.py` 的约定，**凡是 `/` 开头都会走系统命令分支**。

例如用户输入：

```text
/ping 127.0.0.1
```

后端实际执行：

```python
await execute_shell_stream("ping 127.0.0.1", ui)
```

也就是说，你只要输入形如：

```text
/ping ...
```

就会走命令执行逻辑。

> 如果你想把 `/ping` 单独映射成固定目标，也可以在你自己的后端里再加一层命令解析。

---

#### 案例 3：执行需要交互输入的脚本

用户输入：

```text
/python3 examples/test_stdin.py
```

后端执行：

```python
await execute_shell_stream("python3 examples/test_stdin.py", ui)
```

当前端看到脚本输出：

```text
👉 请输入你的名字:
```

用户继续输入：

```text
Alice
```

这次 `Alice` 不会传给后端入口，而是会直接写入脚本 stdin，脚本再继续输出结果。

---

## 6. 后端最小实现模板

如果你要自己写一个新后端，建议直接按下面写：

```python
from src.tui.application import AgentCLI
from src.tui.cmd_processor import execute_shell_stream


async def my_controller(user_input: str, ui: AgentCLI) -> None:
    try:
        # 1) / 开头 => 系统命令
        if user_input.startswith("/"):
            command = user_input[1:].strip()
            await execute_shell_stream(command, ui)
            return

        # 2) 普通文本 => 走你的模型/业务逻辑
        ui.output_system("模型思考中...", style="dim")

        # 这里替换成你的真实后端逻辑
        answer = f"## 已收到输入\n\n你输入的是：`{user_input}`"

        ui.output_llm(answer, markdown=True)

    except Exception as exc:
        ui.output_system(f"ERROR: 后端处理失败: {exc}")
```

挂载方式：

```python
from src.tui.application import AgentCLI

app = AgentCLI(command_handler=my_controller)
app.run()
```

---

## 7. 后端同事只需要记住这 3 件事

### 1）输入从这里进

```python
async def command_handler(user_input: str, ui: AgentCLI) -> None:
```

### 2）输出从这里回

```python
ui.output_system(...)
ui.output_llm(...)
```

### 3）系统命令优先复用这个

```python
await execute_shell_stream(command, ui)
```

---

## 8. 一句话总结

这套 TUI 的对接方式非常简单：

- **前端把用户输入作为 `user_input` 传给后端**
- **后端通过 `ui.output_system()` / `ui.output_llm()` 把结果写回界面**
- **如果要执行命令，直接复用 `execute_shell_stream()`**

