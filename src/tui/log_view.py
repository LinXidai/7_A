"""日志显示相关组件"""

import re

from rich.console import RenderableType
from rich.highlighter import ReprHighlighter
from rich.markdown import Markdown
from rich.pretty import Pretty
from rich.syntax import Syntax
from rich.text import Text
from textual.binding import Binding
from textual.selection import Selection
from textual.strip import Strip
from textual.widgets import RichLog

ERROR_PATTERN = re.compile(r"(?i)\berror\b")
MARKDOWN_PATTERN = re.compile(
    r"(^#{1,6}\s)|(^>\s)|(^[-*+]\s)|(^\d+\.\s)|(```)|(`[^`]+`)|(\*\*[^*]+\*\*)|(__[^_]+__)|(\[[^\]]+\]\([^)]+\))",
    re.MULTILINE,
)


def stylize_error_keywords(text: Text) -> Text:
    """将文本中的 error 关键字标红"""
    for match in ERROR_PATTERN.finditer(text.plain):
        text.stylize("bold red", *match.span())
    return text


class AgentRichLog(RichLog):
    """用户输入、系统输出、LLM 输出的日志组件"""

    ALLOW_SELECT = True
    BINDINGS = RichLog.BINDINGS + [
        Binding("ctrl+c", "copy_selection", "Copy", show=True, priority=True),
    ]
    markdown_theme = "github-dark"
    syntax_theme = "github-dark"

    def __init__(self, *args, **kwargs) -> None:
        kwargs.setdefault("highlight", False)
        kwargs.setdefault("markup", False)
        kwargs.setdefault("wrap", True)
        super().__init__(*args, **kwargs)
        # 高亮器
        self.llm_highlighter = ReprHighlighter()
        # 保留每一行的纯文本，供复制使用
        self._plain_lines: list[str] = []

    @staticmethod
    def is_markdown(content: str) -> bool:
        stripped = content.strip()
        if not stripped:
            return False
        return bool(MARKDOWN_PATTERN.search(stripped))

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """仅在日志存在选区时显示/启用 Copy"""
        if action == "copy_selection":
            selection = self.text_selection
            return selection is not None and bool(self.get_selection(selection))
        return True

    def write_user_message(self, content: str) -> None:
        """用户输入：只回显，不做 markdown / error / 语法高亮"""
        prompt = Text("> ", style="bold green")
        prompt.append(content)
        self.write(prompt)

    def write_system_message(self, content: str, style: str = "") -> None:
        """系统输出：保留样式并将 error 标红"""
        self.write(stylize_error_keywords(Text(content, style=style)))

    def build_llm_renderable(
        self,
        content: RenderableType | object,
        *,
        markdown: bool | None = None,
        language: str | None = None,
    ) -> RenderableType:
        if isinstance(content, str):
            if language:
                return Syntax(
                    content.rstrip("\n"),
                    language,
                    theme=self.syntax_theme,
                    word_wrap=True,
                    line_numbers=False,
                )

            should_render_markdown = markdown if markdown is not None else self.is_markdown(content)
            if should_render_markdown:
                return Markdown(
                    content,
                    code_theme=self.markdown_theme,
                    inline_code_theme=self.syntax_theme,
                )

            text = Text(content)
            self.llm_highlighter.highlight(text)
            return text

        if isinstance(content, Text):
            text = content.copy()
            self.llm_highlighter.highlight(text)
            return text

        if hasattr(content, "__rich_console__") or hasattr(content, "__rich__"):
            return content

        return Pretty(content)

    def write_llm_message(
        self,
        content: RenderableType | object,
        *,
        markdown: bool | None = None,
        language: str | None = None,
    ) -> None:
        """LLM 输出入口：可选 markdown / syntax 高亮"""
        renderable = self.build_llm_renderable(content, markdown=markdown, language=language)
        self.write(renderable)

    def _sync_plain_lines(self) -> None:
        """同步当前日志的纯文本行，用于选择与复制is_markdown"""
        self._plain_lines = [
            "".join(segment.text for segment in strip if not segment.control)
            for strip in self.lines
        ]

    def write(self, *args, **kwargs):
        """写入日志后，同步可复制纯文本is_markdown"""
        result = super().write(*args, **kwargs)
        if self._size_known:
            self._sync_plain_lines()
        return result

    def clear(self):
        """清空日志与纯文本缓存is_markdown"""
        result = super().clear()
        self._plain_lines.clear()
        return result

    def get_selection(self, selection: Selection) -> tuple[str, str] | None:
        """返回当前选区中的纯文本is_markdown"""
        if not self._plain_lines:
            return None
        return selection.extract("\n".join(self._plain_lines)), "\n"

    def selection_updated(self, selection: Selection | None) -> None:
        """选区变化时刷新渲染与快捷键状态is_markdown"""
        self._line_cache.clear()
        self.refresh()
        self.refresh_bindings()

    def action_copy_selection(self) -> None:
        """复制当前日志选区"""
        self.screen.action_copy_text()

    def _render_line(self, y: int, scroll_x: int, width: int) -> Strip:
        """渲染单行，并在选择时应用选中高亮is_markdown"""
        if y >= len(self.lines):
            return Strip.blank(width, self.rich_style)

        selection = self.text_selection
        cache_key = (y + self._start_line, scroll_x, width, self._widest_line_width)
        if selection is None and cache_key in self._line_cache:
            return self._line_cache[cache_key]

        line = self.lines[y]
        if selection is not None:
            text = Text()
            for segment in line:
                if segment.control:
                    continue
                text.append(segment.text, segment.style)

            if (select_span := selection.get_span(y)) is not None:
                start, end = select_span
                if end == -1:
                    end = len(text)
                text.stylize(self.screen.selection_style, start, end)

            line = Strip(text.render(self.app.console), text.cell_len)

        line = line.crop_extend(scroll_x, scroll_x + width, self.rich_style).apply_offsets(scroll_x, y)

        if selection is None:
            self._line_cache[cache_key] = line
        return line
