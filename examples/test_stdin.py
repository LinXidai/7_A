# test_interactive.py
import time

print("程序已启动...")
time.sleep(1)

# 这行代码会导致程序挂起，等待 stdin 输入
name = input("👉 请输入你的名字: ")

print(f"你好, {name}! 欢迎来到多 Agent 系统。")
time.sleep(1)
print("程序执行完毕。")