"""
停止所有跟单相关进程。

运行方式:
  python scripts/stop_all.py
"""

import sys
import os
import subprocess


def main():
    print("停止所有跟单进程...")

    # 方法 1: 按进程名杀
    scripts = ["monitor.py", "executor.py"]
    for script in scripts:
        try:
            # Windows: taskkill
            result = subprocess.run(
                ["taskkill", "/F", "/FI",
                 f"IMAGENAME eq python.exe", "/FI",
                 f"WINDOWTITLE eq *{script}*"],
                capture_output=True, text=True
            )
        except Exception:
            pass

    # 方法 2: 更可靠的方式 — 用 Python 查找
    try:
        result = subprocess.run(
            ["wmic", "process", "where",
             "commandline like '%monitor.py%' or commandline like '%executor.py%'",
             "get", "processid"],
            capture_output=True, text=True
        )
        for line in result.stdout.splitlines():
            line = line.strip()
            if line and line.isdigit():
                pid = int(line)
                try:
                    subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                                   capture_output=True)
                    print(f"  已终止 PID={pid}")
                except Exception:
                    pass
    except Exception:
        pass

    print("  完成")


if __name__ == "__main__":
    main()
