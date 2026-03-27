import re

from textual.binding import Binding
from rich.console import Console, ConsoleOptions, RenderResult, RenderableType
from rich.highlighter import Highlighter, ReprHighlighter
from rich.markdown import Markdown
from rich.measure import Measurement
from rich.pretty import Pretty
from rich.syntax import Syntax
from rich.text import Text
from textual.app import App, ComposeResult
from textual.widgets import Footer, Header, Input, RichLog

ERROR_PATTERN = re.compile(r"(?i)\berror\b")
MARKDOWN_PATTERN = re.compile(
    r"(^#{1,6}\s)|(^>\s)|(^[-*+]\s)|(^\d+\.\s)|(```)|(`[^`]+`)|(\*\*[^*]+\*\*)|(__[^_]+__)|(\[[^\]]+\]\([^)]+\))",
    re.MULTILINE,
)


class LLMOutputHighlighter(Highlighter):
    """LLM 文本输出高亮：保留 Rich 默认高亮，并将 error 标红"""

    def __init__(self) -> None:
        self._repr_highlighter = ReprHighlighter()

    def highlight(self, text: Text) -> None:
        self._repr_highlighter.highlight(text)
        for match in ERROR_PATTERN.finditer(text.plain):
            text.stylize("bold red", *match.span())


class ErrorKeywordRenderable:
    """包装 renderable，在最终渲染结果中将 error 关键字标红"""

    def __init__(self, renderable: RenderableType) -> None:
        self.renderable = renderable

    def __rich_measure__(self, console: Console, options: ConsoleOptions) -> Measurement:
        return Measurement.get(console, options, self.renderable)

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        lines = console.render_lines(self.renderable, options, pad=False)

        for index, line in enumerate(lines):
            text = Text()
            for segment in line:
                if segment.control:
                    continue
                text.append(segment.text, segment.style)

            for match in ERROR_PATTERN.finditer(text.plain):
                text.stylize("bold red", *match.span())

            yield text


class AgentRichLog(RichLog):
    """用户输入与 LLM 输出的日志组件"""

    markdown_theme = "github-dark"
    syntax_theme = "github-dark"

    def __init__(self, *args, **kwargs) -> None:
        kwargs.setdefault("highlight", False)
        kwargs.setdefault("markup", False)
        kwargs.setdefault("wrap", True)
        super().__init__(*args, **kwargs)
        self.llm_highlighter = LLMOutputHighlighter()

    @staticmethod
    def looks_like_markdown(content: str) -> bool:
        stripped = content.strip()
        if not stripped:
            return False
        return bool(MARKDOWN_PATTERN.search(stripped))

    def write_user_message(self, content: str) -> None:
        """用户输入：只回显，不做 markdown / error / 语法高亮"""
        prompt = Text("> ", style="bold green")
        prompt.append(content)
        super().write(prompt)

    def write_system_message(self, content: str, style: str = "") -> None:
        super().write(Text(content, style=style))

    def build_llm_renderable(
        self,
        content: RenderableType | object,
        *,
        markdown: bool | None = None,
        language: str | None = None,
    ) -> RenderableType:
        if isinstance(content, str):
            if language:
                return ErrorKeywordRenderable(
                    Syntax(
                        content.rstrip("\n"),
                        language,
                        theme=self.syntax_theme,
                        word_wrap=True,
                        line_numbers=False,
                    )
                )

            should_render_markdown = markdown if markdown is not None else self.looks_like_markdown(content)
            if should_render_markdown:
                return ErrorKeywordRenderable(
                    Markdown(
                        content,
                        code_theme=self.markdown_theme,
                        inline_code_theme=self.syntax_theme,
                    )
                )

            text = Text(content)
            self.llm_highlighter.highlight(text)
            return text

        if isinstance(content, Text):
            text = content.copy()
            self.llm_highlighter.highlight(text)
            return text

        if hasattr(content, "__rich_console__") or hasattr(content, "__rich__"):
            return ErrorKeywordRenderable(content)

        return ErrorKeywordRenderable(Pretty(content))

    def write_llm_message(
        self,
        content: RenderableType | object,
        *,
        markdown: bool | None = None,
        language: str | None = None,
    ) -> None:
        """预留给 LLM 输出的入口：这里会启用 markdown / syntax / error 高亮"""
        renderable = self.build_llm_renderable(content, markdown=markdown, language=language)
        super().write(renderable)


class CommandInput(Input):
    """支持上下键遍历历史命令的输入框"""

    BINDINGS = Input.BINDINGS + [
        Binding("up", "history_previous", "Previous history", show=False),
        Binding("down", "history_next", "Next history", show=False),
    ]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.history: list[str] = []
        self.history_index: int | None = None
        self.history_draft = ""

    def add_to_history(self, command: str) -> None:
        """记录一条已提交命令，并重置历史浏览状态"""
        if not command:
            return
        self.history.append(command)
        self.history_index = None
        self.history_draft = ""

    def _load_history_value(self, value: str) -> None:
        self.value = value
        self.cursor_position = len(value)

    def action_history_previous(self) -> None:
        """切到上一条历史命令"""
        if not self.history:
            self.app.bell()
            return

        if self.history_index is None:
            self.history_draft = self.value
            self.history_index = len(self.history) - 1
        elif self.history_index > 0:
            self.history_index -= 1
        else:
            self.app.bell()

        self._load_history_value(self.history[self.history_index])

    def action_history_next(self) -> None:
        """切到下一条历史命令，或返回当前草稿"""
        if self.history_index is None:
            self.app.bell()
            return

        if self.history_index < len(self.history) - 1:
            self.history_index += 1
            self._load_history_value(self.history[self.history_index])
        else:
            self.history_index = None
            self._load_history_value(self.history_draft)


class AgentCLI(App):
    """多 Agent CLI 系统的 TUI 界面"""

    CSS = """
    Screen {
        layout: vertical;
    }

    #log_area {
        height: 1fr;
        border: solid green;
    }

    #command_input {
        dock: bottom;
    }
    """

    BINDINGS = [("ctrl+c", "quit", "Quit")]

    def __init__(self, driver_class=None, css_path=None, watch_css=False, ansi_color=False):
        super().__init__(driver_class, css_path, watch_css, ansi_color)

    def compose(self) -> ComposeResult:
        """构建 UI 组件树"""
        yield Header(show_clock=True)
        yield AgentRichLog(id="log_area")
        yield CommandInput(placeholder="请输入命令或自然语言...", id="command_input")
        yield Footer()

    def on_ready(self) -> None:
        """UI 加载完成后的初始化操作"""
        log_area = self.query_one("#log_area", AgentRichLog)
        log_area.write_system_message("欢迎来到多 Agent CLI 系统！", style="bold cyan")
        log_area.write_system_message("系统初始化完成，等待输入...")
        self.query_one("#command_input", CommandInput).focus()

    def handle_llm_output(
        self,
        content: str,
        *,
        markdown: bool | None = None,
        language: str | None = None,
    ) -> None:
        """预留给后续 LLM 响应接入的统一入口"""
        log_area = self.query_one("#log_area", AgentRichLog)
        log_area.write_llm_message(content, markdown=markdown, language=language)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """处理用户在输入框按下回车键的事件"""
        user_input = event.value.strip()
        if not user_input:
            return

        input_widget = self.query_one("#command_input", CommandInput)
        log_area = self.query_one("#log_area", AgentRichLog)

        input_widget.add_to_history(user_input)
        log_area.write_user_message(user_input)

        # 输入后清空输入框
        input_widget.value = ""

        # LLM 输出测试：
        llm_text = """
好的，这是一个 Python 打印示例：
```python
def hello_world():
    print("Hello, Agent!")
```
ERROR
"""
        self.handle_llm_output(llm_text)

        # 系统输出测试：
        sys_text = """
PING a.w.bilicdn1.com (120.192.82.75) 56(84) bytes of data.
64 bytes from 120.192.82.75 (120.192.82.75): icmp_seq=1 ttl=53 time=20.7 ms
64 bytes from 120.192.82.75 (120.192.82.75): icmp_seq=2 ttl=53 time=91.0 ms
64 bytes from 120.192.82.75 (120.192.82.75): icmp_seq=3 ttl=53 time=25.2 ms
64 bytes from 120.192.82.75 (120.192.82.75): icmp_seq=4 ttl=53 time=21.0 ms
64 bytes from 120.192.82.75 (120.192.82.75): icmp_seq=5 ttl=53 time=24.0 ms
64 bytes from 120.192.82.75 (120.192.82.75): icmp_seq=6 ttl=53 time=21.2 ms
64 bytes from 120.192.82.75 (120.192.82.75): icmp_seq=7 ttl=53 time=21.1 ms
64 bytes from 120.192.82.75 (120.192.82.75): icmp_seq=8 ttl=53 time=21.2 ms
"""
        log_area.write_system_message(sys_text)


if __name__ == "__main__":
    app = AgentCLI()
    app.run()
