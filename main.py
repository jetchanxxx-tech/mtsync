"""
MT 跟单同步系统 — 统一入口。

用法:
  python main.py                 启动 GUI 界面
  python main.py --monitor       启动信号监控进程
  python main.py --executor      启动跟单执行进程

商业化打包后:
  MT_Copy_Trading.exe            启动 GUI
  MT_Copy_Trading.exe --monitor  信号监控（由 GUI 自动启动）
  MT_Copy_Trading.exe --executor 跟单执行（由 GUI 自动启动）
"""

import sys
import os
import argparse


def run_gui():
    """启动 GUI 界面"""
    from scripts.gui import main as gui_main
    gui_main()


def run_monitor():
    """启动信号监控进程"""
    from scripts.monitor import main as monitor_main
    sys.exit(monitor_main() or 0)


def run_executor():
    """启动跟单执行进程"""
    from scripts.executor import main as executor_main
    sys.exit(executor_main() or 0)


def main():
    # PyInstaller 打包后的资源路径
    if getattr(sys, 'frozen', False):
        os.chdir(os.path.dirname(sys.executable))

    parser = argparse.ArgumentParser(description="MT Copy Trading System")
    parser.add_argument("--monitor", action="store_true", help="Run as signal monitor process")
    parser.add_argument("--executor", action="store_true", help="Run as copy executor process")
    parser.add_argument("--cli", action="store_true", help="Run CLI (non-GUI)")

    # 也支持位置参数（兼容简写）
    args, unknown = parser.parse_known_args()

    if args.monitor:
        run_monitor()
    elif args.executor:
        run_executor()
    elif args.cli:
        print("CLI mode not implemented yet. Use GUI or --monitor/--executor.")
    else:
        run_gui()


if __name__ == "__main__":
    main()
