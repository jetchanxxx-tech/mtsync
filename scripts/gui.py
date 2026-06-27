"""
MT 跟单系统 GUI — Tkinter 双页界面。

页 1: 配置向导 — 设置信号源和跟单目标的 MT5 路径
页 2: 监控面板 — 实时 debug 滚动、分类筛选、日志保存、进程控制

运行方式:
  python scripts/gui.py
"""

import sys
import os
import re
import json
import time
import queue
import threading
import subprocess
import signal
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(ROOT, "config", "gui_config.json")
MONITOR_SCRIPT = os.path.join(ROOT, "scripts", "monitor.py")
EXECUTOR_SCRIPT = os.path.join(ROOT, "scripts", "executor.py")

# 打包后统一入口：MT_Copy_Trading.exe --monitor / --executor
_MAIN_SCRIPT = os.path.join(ROOT, "main.py")
if getattr(sys, 'frozen', False):
    MONITOR_CMD = [sys.executable, "--monitor"]
    EXECUTOR_CMD = [sys.executable, "--executor"]
else:
    MONITOR_CMD = [sys.executable, _MAIN_SCRIPT, "--monitor"]
    EXECUTOR_CMD = [sys.executable, _MAIN_SCRIPT, "--executor"]

# ---- 颜色主题 ----
C_BG = "#1a1a2e"
C_FG = "#e0e0e0"
C_ACCENT = "#00d4aa"
C_WARN = "#ffaa00"
C_ERROR = "#ff4444"
C_INFO = "#4da6ff"
C_SUCCESS = "#00d4aa"
C_PANEL = "#16213e"
C_ENTRY = "#0f3460"
C_BUTTON = "#00d4aa"
C_BUTTON_FG = "#1a1a2e"
C_BUTTON_DANGER = "#ff4444"

# 日志分类关键词
CATEGORY_PATTERNS = {
    "通讯": [r"ZMQ", r"PUB", r"SUB", r"绑定", r"连接", r"tcp://"],
    "同步": [r"OPEN", r"CLOSE", r"MODIFY", r"跟单", r"master=", r"follower=",
            r"持仓", r"信号"],
    "进程": [r"启动", r"停止", r"PID", r"心跳", r"轮询", r"运行", r"STOP"],
    "状态": [r"成功", r"失败", r"跳过", r"SUCCESS", r"FAILED", r"SKIPPED",
             r"风控", r"拒绝"],
}


def classify_line(line: str) -> list[str]:
    """将日志行分类到对应类别"""
    cats = []
    for cat, patterns in CATEGORY_PATTERNS.items():
        for pat in patterns:
            if re.search(pat, line, re.IGNORECASE):
                cats.append(cat)
                break
    return cats if cats else ["其他"]


class SetupPage(ttk.Frame):
    """第 1 页：配置向导"""

    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller
        self.follower_entries: list[tuple[tk.StringVar, tk.StringVar]] = []

        self._build_ui()
        self._load_config()

    def _build_ui(self):
        # 标题
        title = tk.Label(self, text="MT 跟单同步系统", font=("Microsoft YaHei", 18, "bold"),
                         bg=C_BG, fg=C_ACCENT)
        title.pack(pady=(30, 5))

        subtitle = tk.Label(self, text="配置信号源和跟单目标", font=("Microsoft YaHei", 10),
                            bg=C_BG, fg=C_FG)
        subtitle.pack(pady=(0, 30))

        # ---- 信号源配置 ----
        lead_frame = tk.LabelFrame(self, text="信号源 (Lead)", font=("Microsoft YaHei", 11, "bold"),
                                   bg=C_PANEL, fg=C_ACCENT, padx=15, pady=15)
        lead_frame.pack(fill="x", padx=40, pady=(0, 15))

        tk.Label(lead_frame, text="MT5 路径:", bg=C_PANEL, fg=C_FG,
                 font=("Consolas", 10)).grid(row=0, column=0, sticky="w")
        self.lead_path = tk.StringVar()
        lead_entry = tk.Entry(lead_frame, textvariable=self.lead_path, width=55,
                              bg=C_ENTRY, fg=C_FG, insertbackground=C_FG,
                              font=("Consolas", 10), relief="flat")
        lead_entry.grid(row=0, column=1, padx=(10, 10), pady=5)
        tk.Button(lead_frame, text="浏览...", command=self._browse_lead,
                  bg=C_BUTTON, fg=C_BUTTON_FG, font=("Microsoft YaHei", 9),
                  relief="flat", padx=12, cursor="hand2").grid(row=0, column=2)

        # ---- 跟单目标配置 ----
        self.follow_frame = tk.LabelFrame(self, text="跟单目标 (Follower)",
                                          font=("Microsoft YaHei", 11, "bold"),
                                          bg=C_PANEL, fg=C_ACCENT, padx=15, pady=15)
        self.follow_frame.pack(fill="x", padx=40, pady=(0, 20))

        # 默认一行
        self._add_follower_row()

        # + 按钮
        add_btn = tk.Button(self.follow_frame, text="＋ 添加跟单账户",
                            command=self._add_follower_row,
                            bg=C_BUTTON, fg=C_BUTTON_FG,
                            font=("Microsoft YaHei", 10, "bold"),
                            relief="flat", padx=20, pady=5, cursor="hand2")
        add_btn.grid(row=99, column=0, columnspan=3, pady=(10, 0), sticky="w")

        # ---- 启动按钮 ----
        btn_frame = tk.Frame(self, bg=C_BG)
        btn_frame.pack(pady=20)
        tk.Button(btn_frame, text="→  启动系统",
                  command=self._on_start,
                  bg=C_ACCENT, fg=C_BUTTON_FG,
                  font=("Microsoft YaHei", 13, "bold"),
                  relief="flat", padx=40, pady=10, cursor="hand2").pack()

    def _add_follower_row(self):
        row = len(self.follower_entries)
        path_var = tk.StringVar()
        note_var = tk.StringVar(value=f"从账户 {row + 1}")

        r = row + 1  # grid row, offset for header

        tk.Label(self.follow_frame, textvariable=note_var, bg=C_PANEL, fg=C_FG,
                 font=("Consolas", 10), width=12, anchor="e").grid(row=r, column=0, padx=(0, 10), pady=3)

        entry = tk.Entry(self.follow_frame, textvariable=path_var, width=50,
                         bg=C_ENTRY, fg=C_FG, insertbackground=C_FG,
                         font=("Consolas", 10), relief="flat")
        entry.grid(row=r, column=1, padx=(0, 5), pady=3)

        tk.Button(self.follow_frame, text="浏览", command=lambda v=path_var: self._browse_follower(v),
                  bg="#3a3a5c", fg=C_FG, font=("Microsoft YaHei", 8),
                  relief="flat", padx=8, cursor="hand2").grid(row=r, column=2, padx=(0, 5))

        if row > 0:
            tk.Button(self.follow_frame, text="✕",
                      command=lambda idx=row: self._remove_follower_row(idx),
                      bg=C_ERROR, fg="white", font=("Microsoft YaHei", 9, "bold"),
                      relief="flat", padx=6, cursor="hand2").grid(row=r, column=3)

        self.follower_entries.append((path_var, note_var))

    def _remove_follower_row(self, idx: int):
        if idx < len(self.follower_entries):
            self.follower_entries.pop(idx)
        # 重建 UI
        for w in self.follow_frame.winfo_children():
            w.destroy()
        # 重建 header
        tk.Label(self.follow_frame, text="", bg=C_PANEL).grid(row=0, column=0)  # spacer
        old_entries = self.follower_entries[:]
        self.follower_entries = []
        for path_var, note_var in old_entries:
            self._add_follower_row()
            self.follower_entries[-1] = (path_var, note_var)

    def _browse_lead(self):
        path = filedialog.askopenfilename(
            title="选择信号源 MT5 terminal64.exe",
            filetypes=[("MT5 Terminal", "terminal64.exe"), ("All", "*.*")]
        )
        if path:
            self.lead_path.set(path)

    def _browse_follower(self, var: tk.StringVar):
        path = filedialog.askopenfilename(
            title="选择跟单 MT5 terminal64.exe",
            filetypes=[("MT5 Terminal", "terminal64.exe"), ("All", "*.*")]
        )
        if path:
            var.set(path)

    def _get_config(self) -> dict:
        followers = [p.get() for p, _ in self.follower_entries if p.get().strip()]
        return {
            "lead_path": self.lead_path.get().strip(),
            "follower_paths": followers,
        }

    def _on_start(self):
        cfg = self._get_config()
        if not cfg["lead_path"]:
            messagebox.showwarning("配置不完整", "请设置信号源 MT5 路径")
            return
        if not cfg["follower_paths"]:
            messagebox.showwarning("配置不完整", "请至少添加一个跟单目标")
            return
        self._save_config()
        self.controller.switch_to_monitor(cfg)

    def _save_config(self):
        cfg = self._get_config()
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)

    def _load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                self.lead_path.set(cfg.get("lead_path", ""))
                for fp in cfg.get("follower_paths", []):
                    if len(self.follower_entries) == 1 and not self.follower_entries[0][0].get():
                        self.follower_entries[0][0].set(fp)
                    else:
                        self._add_follower_row()
                        self.follower_entries[-1][0].set(fp)
            except Exception:
                pass


class MonitorPage(ttk.Frame):
    """第 2 页：监控面板"""

    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller
        self.config: dict = {}
        self.processes: list[subprocess.Popen] = []
        self.log_queue = queue.Queue()
        self.running = False
        self.log_lines: list[tuple[str, list[str]]] = []  # (line, categories)

        # 筛选状态
        self.filter_vars: dict[str, tk.BooleanVar] = {}
        for cat in CATEGORY_PATTERNS:
            self.filter_vars[cat] = tk.BooleanVar(value=True)
        self.filter_vars["其他"] = tk.BooleanVar(value=True)

        self._build_ui()

    def _build_ui(self):
        # ---- 顶栏 ----
        topbar = tk.Frame(self, bg=C_PANEL, height=50)
        topbar.pack(fill="x")
        topbar.pack_propagate(False)

        tk.Label(topbar, text="跟单系统监控", font=("Microsoft YaHei", 13, "bold"),
                 bg=C_PANEL, fg=C_ACCENT).pack(side="left", padx=20, pady=10)

        # 状态指示灯
        self.status_light = tk.Canvas(topbar, width=12, height=12, bg=C_PANEL, highlightthickness=0)
        self.status_light.pack(side="left", padx=(0, 5))
        self._draw_light("gray")

        self.status_label = tk.Label(topbar, text="未启动", font=("Microsoft YaHei", 9),
                                     bg=C_PANEL, fg=C_FG)
        self.status_label.pack(side="left")

        # 右侧按钮
        btn_frame = tk.Frame(topbar, bg=C_PANEL)
        btn_frame.pack(side="right", padx=15)

        tk.Button(btn_frame, text="保存日志", command=self._save_logs,
                  bg="#3a3a5c", fg=C_FG, font=("Microsoft YaHei", 9),
                  relief="flat", padx=12, pady=4, cursor="hand2").pack(side="left", padx=5)
        tk.Button(btn_frame, text="返回配置", command=self.controller.switch_to_setup,
                  bg="#3a3a5c", fg=C_FG, font=("Microsoft YaHei", 9),
                  relief="flat", padx=12, pady=4, cursor="hand2").pack(side="left", padx=5)
        tk.Button(btn_frame, text="退出", command=self._on_exit,
                  bg=C_ERROR, fg="white", font=("Microsoft YaHei", 9, "bold"),
                  relief="flat", padx=15, pady=4, cursor="hand2").pack(side="left", padx=5)

        # ---- 主区域 ----
        main_area = tk.Frame(self, bg=C_BG)
        main_area.pack(fill="both", expand=True, padx=15, pady=(10, 5))

        # 左侧：筛选面板
        filter_panel = tk.LabelFrame(main_area, text="筛选", font=("Microsoft YaHei", 9),
                                     bg=C_PANEL, fg=C_FG, padx=8, pady=8)
        filter_panel.pack(side="left", fill="y", padx=(0, 10))

        for cat in CATEGORY_PATTERNS:
            cb = tk.Checkbutton(filter_panel, text=cat, variable=self.filter_vars[cat],
                                command=self._apply_filter,
                                bg=C_PANEL, fg=C_FG, selectcolor=C_ENTRY,
                                activebackground=C_PANEL, activeforeground=C_ACCENT,
                                font=("Microsoft YaHei", 9))
            cb.pack(anchor="w", pady=2)

        # 分隔
        ttk.Separator(filter_panel, orient="horizontal").pack(fill="x", pady=5)

        tk.Checkbutton(filter_panel, text="其他", variable=self.filter_vars["其他"],
                       command=self._apply_filter,
                       bg=C_PANEL, fg=C_FG, selectcolor=C_ENTRY,
                       activebackground=C_PANEL, activeforeground=C_ACCENT,
                       font=("Microsoft YaHei", 9)).pack(anchor="w", pady=2)

        # 统计
        ttk.Separator(filter_panel, orient="horizontal").pack(fill="x", pady=8)
        self.stats_text = tk.Label(filter_panel, text="统计: -\n成功: 0\n失败: 0\n跳过: 0",
                                   bg=C_PANEL, fg=C_FG, font=("Consolas", 9),
                                   justify="left")
        self.stats_text.pack(anchor="w")

        ttk.Separator(filter_panel, orient="horizontal").pack(fill="x", pady=8)
        self.latency_label = tk.Label(filter_panel,
                                      text="延迟\n最后: -- ms\n平均: -- ms\n最小: -- ms",
                                      bg=C_PANEL, fg=C_ACCENT, font=("Consolas", 9, "bold"),
                                      justify="left")
        self.latency_label.pack(anchor="w")

        # 右侧：日志区
        log_frame = tk.Frame(main_area, bg=C_BG)
        log_frame.pack(side="left", fill="both", expand=True)

        self.log_widget = scrolledtext.ScrolledText(
            log_frame, wrap="word", bg="#0a0a1a", fg=C_FG,
            insertbackground=C_FG, font=("Consolas", 9),
            relief="flat", borderwidth=0,
            state="disabled",
        )
        self.log_widget.pack(fill="both", expand=True)

        # 配置颜色标签
        for cat in CATEGORY_PATTERNS:
            color = {
                "通讯": "#4da6ff", "同步": C_SUCCESS, "进程": "#aa88ff",
                "状态": C_WARN,
            }.get(cat, C_FG)
            self.log_widget.tag_config(cat, foreground=color)
        self.log_widget.tag_config("其他", foreground="#888888")
        self.log_widget.tag_config("timestamp", foreground="#666666")

        # ---- 进程信息栏 ----
        self.proc_bar = tk.Frame(self, bg=C_PANEL, height=28)
        self.proc_bar.pack(fill="x", padx=15, pady=(0, 10))
        self.proc_bar.pack_propagate(False)
        self.proc_label = tk.Label(self.proc_bar, text="进程: 等待启动...",
                                   bg=C_PANEL, fg="#888888", font=("Consolas", 9))
        self.proc_label.pack(side="left", padx=15, pady=4)

    # ---- 进程管理 ----

    def set_config(self, config: dict):
        self.config = config

    def start_processes(self):
        if self.running:
            return

        # 清空旧日志
        self.log_lines.clear()
        self.log_widget.configure(state="normal")
        self.log_widget.delete("1.0", "end")
        self.log_widget.configure(state="disabled")

        self.running = True
        self._add_log("系统启动中...", ["进程"])
        self._draw_light(C_WARN)
        self.status_label.config(text="启动中...", fg=C_WARN)

        # 启动 monitor（需要传 lead_path）
        try:
            p = subprocess.Popen(
                [*MONITOR_CMD],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                env={**os.environ, "LEAD_OVERRIDE_PATH": self.config["lead_path"]},
            )
            self.processes.append(("monitor", p))
        except Exception as e:
            self._add_log(f"monitor start failed: {e}", ["process", "status"])

        # 启动 executor（每个跟单目标一个）
        for i, fp in enumerate(self.config.get("follower_paths", [])):
            try:
                p = subprocess.Popen(
                    [*EXECUTOR_CMD],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace",
                    env={**os.environ, "FOLLOWER_OVERRIDE_PATH": fp},
                )
                self.processes.append((f"executor-{i+1}", p))
            except Exception as e:
                self._add_log(f"executor-{i+1} start failed: {e}", ["process", "status"])

        # 启动日志读取线程
        threading.Thread(target=self._read_outputs, daemon=True).start()
        # 定期刷新 UI
        self._poll_ui()

        self._add_log(f"系统已启动: 1 个 monitor + {len(self.config.get('follower_paths', []))} 个 executor",
                      ["进程"])
        self._draw_light(C_SUCCESS)
        self.status_label.config(text="运行中", fg=C_SUCCESS)
        self._update_proc_bar()

    def stop_processes(self):
        self.running = False
        for name, p in self.processes:
            try:
                p.terminate()
            except Exception:
                pass
        # 等最多 3 秒
        for name, p in self.processes:
            try:
                p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    p.kill()
                except Exception:
                    pass
        self.processes.clear()
        self._add_log("所有进程已停止", ["进程"])
        self._draw_light("gray")
        self.status_label.config(text="已停止", fg="#888888")
        self._update_proc_bar()

    def _read_outputs(self):
        """后台线程读取子进程输出"""
        while self.running:
            for name, p in list(self.processes):
                if p.poll() is not None:
                    if p.returncode != 0 and self.running:
                        self.log_queue.put(("error", f"[{name}] 进程退出 (code={p.returncode})"))
                    continue
                try:
                    line = p.stdout.readline()
                    if line:
                        self.log_queue.put((name, line.rstrip()))
                except Exception:
                    pass
            time.sleep(0.1)

    def _poll_ui(self):
        """定时刷新 UI"""
        try:
            while not self.log_queue.empty():
                source, line = self.log_queue.get_nowait()
                if source == "error":
                    self._add_log(line, ["进程", "状态"])
                    continue
                cats = classify_line(line)
                self._add_log(f"[{source}] {line}", cats)
        except Exception:
            pass

        # 更新统计
        self._update_stats()

        if self.running:
            self.after(200, self._poll_ui)

    # ---- 日志操作 ----

    def _add_log(self, line: str, categories: list[str]):
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        full_line = f"{timestamp} {line}"
        self.log_lines.append((full_line, categories))

        # 检查筛选
        if not self._line_visible(categories):
            return

        self.log_widget.configure(state="normal")
        self.log_widget.insert("end", f"{timestamp} ", "timestamp")
        cat = categories[0] if categories else "其他"
        self.log_widget.insert("end", f"{line}\n", cat)
        self.log_widget.see("end")
        self.log_widget.configure(state="disabled")

    def _line_visible(self, categories: list[str]) -> bool:
        for cat in categories:
            if self.filter_vars.get(cat, tk.BooleanVar(value=True)).get():
                return True
        return False

    def _apply_filter(self):
        """重新应用筛选器"""
        self.log_widget.configure(state="normal")
        self.log_widget.delete("1.0", "end")
        for full_line, cats in self.log_lines:
            if self._line_visible(cats):
                parts = full_line.split(" ", 1)
                ts = parts[0]
                content = parts[1] if len(parts) > 1 else ""
                self.log_widget.insert("end", f"{ts} ", "timestamp")
                cat = cats[0] if cats else "其他"
                self.log_widget.insert("end", f"{content}\n", cat)
        self.log_widget.see("end")
        self.log_widget.configure(state="disabled")

    def _save_logs(self):
        path = filedialog.asksaveasfilename(
            title="保存日志",
            defaultextension=".log",
            filetypes=[("Log files", "*.log"), ("Text", "*.txt"), ("All", "*.*")],
            initialfile=f"copy_trade_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log",
        )
        if path:
            with open(path, "w", encoding="utf-8") as f:
                for full_line, _ in self.log_lines:
                    f.write(full_line + "\n")
            self._add_log(f"日志已保存: {path}", ["进程"])

    # ---- UI 更新 ----

    def _update_stats(self):
        success = 0; failed = 0; skipped = 0
        latencies = []

        for line, cats in self.log_lines:
            if "状态" not in cats:
                continue
            if any(kw in line for kw in ["SUCCESS", "跟单成功", "-> follower"]):
                success += 1
            elif any(kw in line for kw in ["FAILED", "失败"]):
                failed += 1
            elif any(kw in line for kw in ["SKIPPED", "跳过"]):
                skipped += 1

            # 提取延迟: LATENCY=XXms
            m = re.search(r'LATENCY=(\d+\.?\d*)ms', line)
            if m:
                latencies.append(float(m.group(1)))

        total_events = success + failed + skipped
        self.stats_text.config(
            text=f"信号事件: {total_events}\n"
                 f"  成功: {success}\n"
                 f"  失败: {failed}\n"
                 f"  跳过: {skipped}"
        )

        if latencies:
            self.latency_label.config(
                text=f"同步延迟\n"
                     f"  最后: {latencies[-1]:.0f} ms\n"
                     f"  平均: {sum(latencies)/len(latencies):.0f} ms\n"
                     f"  最小: {min(latencies):.0f} ms"
            )

    def _update_proc_bar(self):
        proc_names = [name for name, _ in self.processes]
        alive = sum(1 for _, p in self.processes if p.poll() is None)
        total = len(self.processes)
        self.proc_label.config(
            text=f"进程: {', '.join(proc_names) if proc_names else '无'} "
                 f"({alive}/{total} 存活)"
        )

    def _draw_light(self, color: str):
        self.status_light.delete("all")
        self.status_light.create_oval(2, 2, 10, 10, fill=color, outline="")

    def _on_exit(self):
        if self.running:
            if messagebox.askyesno("确认退出", "系统正在运行中，退出将停止所有跟单进程。\n确认退出？"):
                self.stop_processes()
                self.controller.root.destroy()
        else:
            self.controller.root.destroy()


class AppController:
    """主控制器 — 管理页面切换和进程生命周期"""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("MT 跟单同步系统")
        self.root.geometry("1100x750")
        self.root.configure(bg=C_BG)
        self.root.minsize(900, 600)

        # 容器
        self.container = tk.Frame(self.root, bg=C_BG)
        self.container.pack(fill="both", expand=True)

        self.setup_page = SetupPage(self.container, self)
        self.monitor_page = MonitorPage(self.container, self)

        self.setup_page.pack(fill="both", expand=True)

        # 窗口关闭事件
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def switch_to_monitor(self, config: dict):
        self.setup_page.pack_forget()
        self.monitor_page.pack(fill="both", expand=True)
        self.monitor_page.set_config(config)
        # 延迟 500ms 启动，让 UI 先渲染
        self.root.after(500, self.monitor_page.start_processes)

    def switch_to_setup(self):
        if self.monitor_page.running:
            if not messagebox.askyesno("确认返回", "返回配置将停止所有进程。\n确认？"):
                return
            self.monitor_page.stop_processes()
        self.monitor_page.pack_forget()
        self.setup_page.pack(fill="both", expand=True)

    def _on_close(self):
        if self.monitor_page.running:
            if messagebox.askyesno("确认退出", "系统正在运行中，退出将停止所有跟单进程。\n确认退出？"):
                self.monitor_page.stop_processes()
                self.root.destroy()
        else:
            self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    app = AppController()
    app.run()


if __name__ == "__main__":
    main()
