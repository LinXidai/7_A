"""TUI 启动入口"""

from .cmd_processor import main_controller
from .tui import AgentCLI


def main() -> None:
    app = AgentCLI(command_handler=main_controller)
    app.run()


if __name__ == "__main__":
    main()
