"""
一键启动跟单系统（monitor + executor）。

运行方式:
  python scripts/start_all.py

会启动两个子进程:
  1. monitor.py  — 常连信号源终端，检测信号并广播
  2. executor.py — 常连跟单终端，接收信号并执行

支持 1:N：修改 FOLLOWER_PATHS 列表添加更多 executor。
"""

import sys
import os
import time
import subprocess
import signal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MONITOR_SCRIPT = os.path.join(ROOT, "scripts", "monitor.py")
EXECUTOR_SCRIPT = os.path.join(ROOT, "scripts", "executor.py")

# 未来 1:N 扩展：每个 follow 终端一个 executor
# FOLLOWER_PATHS = [
#     r"D:\MetaTrader 5\terminal64.exe",
#     r"E:\MT5_Follower2\terminal64.exe",
# ]

processes: list[subprocess.Popen] = []


def main():
    print("=" * 55)
    print("  MT 跟单系统启动器")
    print(f"  信号源: 711591 (C:\\Program Files\\MetaTrader 5)")
    print(f"  跟单:   711621 (D:\\MetaTrader 5)")
    print("=" * 55)
    print()

    # 启动 monitor
    print("[1/2] 启动信号监控进程...")
    try:
        p_monitor = subprocess.Popen(
            [sys.executable, MONITOR_SCRIPT],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        processes.append(("monitor", p_monitor))
        print(f"  ✓ monitor PID={p_monitor.pid}")
    except Exception as e:
        print(f"  ✗ monitor 启动失败: {e}")
        return 1

    time.sleep(2)  # 等 monitor 启动

    # 启动 executor
    print("[2/2] 启动跟单执行进程...")
    try:
        p_executor = subprocess.Popen(
            [sys.executable, EXECUTOR_SCRIPT],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        processes.append(("executor", p_executor))
        print(f"  ✓ executor PID={p_executor.pid}")
    except Exception as e:
        print(f"  ✗ executor 启动失败: {e}")
        return 1

    print()
    print("  系统已启动！现在去 711591 手动下单测试")
    print("  按 Ctrl+C 停止所有进程")
    print()

    # 等待
    def shutdown(sig=None, frame=None):
        print("\n  正在停止所有进程...")
        for name, p in processes:
            print(f"  停止 {name} (PID={p.pid})...")
            p.terminate()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        print("  系统已停止")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        # 持续打印子进程输出
        while True:
            for name, p in processes:
                if p.poll() is not None:
                    print(f"  [WARN] {name} 进程已退出 (code={p.returncode})")
                    shutdown()
                    return

                # 读取一行输出
                line = p.stdout.readline()
                if line:
                    print(f"  [{name}] {line.rstrip()}")

            time.sleep(0.5)

    except KeyboardInterrupt:
        shutdown()


if __name__ == "__main__":
    main()
