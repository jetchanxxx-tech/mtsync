"""
双终端跟单测试脚本 — 真实的端到端跟单验证。

工作流:
  1. 连接信号源终端 (711591) → 读取持仓 → 断开
  2. 信号检测器对比快照 → 生成事件
  3. 连接跟单终端 (711621) → 执行跟单 → 断开
  4. 循环 (默认每 3 秒一轮)

使用方式:
  python scripts/run_copy_loop.py

前提:
  - C:\Program Files\MetaTrader 5 已登录 711591
  - D:\MetaTrader 5 已登录 711621
"""

import sys
import os
import time
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.dual_terminal import DualTerminalManager
from src.signal_detector import SignalDetector, PositionSnapshot, EventType
from src.copy_executor import (
    CopyConfig, CopyExecutor, CopyResult,
    OrderMappingStore, ResultStatus,
)
from src.risk_manager import RiskManager, RiskConfig

# ============================================================
# 配置
# ============================================================

LEAD_PATH = r"C:\Program Files\MetaTrader 5\terminal64.exe"
FOLLOWER_PATH = r"D:\MetaTrader 5\terminal64.exe"
POLL_INTERVAL_S = 1.0  # 轮询间隔（端到端延迟 ≈ 1.3s）
MAGIC = 888000          # 跟单系统 magic

C = "\033[92m"  # green
R = "\033[91m"  # red
Y = "\033[93m"  # yellow
W = "\033[0m"   # reset


def main():
    print(f"""
{C}╔══════════════════════════════════════════════════╗{W}
{C}║      双终端跟单测试 — 端到端验证                  ║{W}
{C}║                                                  ║{W}
{C}║  信号源: 711591 (手动下单模拟外部信号)             ║{W}
{C}║  跟单目标: 711621 (系统自动跟单)                   ║{W}
{C}║  比例: 1:1  |  风控: 开仓 ≤ 10 个                 ║{W}
{C}╚══════════════════════════════════════════════════╝{W}
""")

    print(f"  {Y}测试步骤:{W}")
    print(f"  1. 本脚本启动信号监控")
    print(f"  2. 你在 {C}711591{Y} MT5 上手动下一笔市价单")
    print(f"  3. 脚本检测到新持仓 → 自动在 {C}711621{Y} 跟单")
    print(f"  4. 你在 711591 上平仓 → 脚本自动在 711621 平仓")
    print()

    # ---- 初始化组件 ----
    mgr = DualTerminalManager(LEAD_PATH, FOLLOWER_PATH)
    detector = SignalDetector()
    config = CopyConfig(
        ratio=1.0,
        magic=MAGIC,
        min_order_interval_ms=500,
    )
    store = OrderMappingStore()
    executor = CopyExecutor(config, store)
    risk = RiskManager(RiskConfig(max_positions=10))

    # ---- 验证两个终端 ----
    print(f"  验证终端连接...")
    if not mgr.connect_lead():
        print(f"  {R}✗ 无法连接信号源终端{W}")
        return
    lead_info = mgr.get_lead_account_info()
    print(f"  {C}✓{W} 信号源: {lead_info.get('login')} | {lead_info.get('server')} | {lead_info.get('balance'):.2f} {lead_info.get('currency')}")
    mgr.disconnect()

    if not mgr.connect_follower():
        print(f"  {R}✗ 无法连接跟单终端{W}")
        return
    follower_info = mgr.get_follower_account_info()
    print(f"  {C}✓{W} 跟单目标: {follower_info.get('login')} | {follower_info.get('server')} | {follower_info.get('balance'):.2f} {follower_info.get('currency')}")

    # 获取跟单终端的品种列表（建映射关系）
    symbols = mgr.get_symbols()
    # 自动建映射: EURUSD → 第一个匹配的品种
    mgr.disconnect()

    # ---- 主循环 ----
    print(f"\n  {Y}开始监控... 轮询间隔 {POLL_INTERVAL_S}s{W}")
    print(f"  现在去 {C}711591{Y} 手动下一笔市价单，我会自动检测并跟单{W}")
    print(f"  按 Ctrl+C 停止\n")

    cycle = 0

    try:
        while True:
            cycle += 1
            # === 阶段 1: 读信号源 ===
            if not mgr.connect_lead():
                print(f"  [{cycle}] {R}连接信号源失败，重试...{W}")
                time.sleep(POLL_INTERVAL_S)
                continue

            positions = mgr.get_lead_positions()
            current_snap = _positions_to_snapshot(positions)
            mgr.disconnect()

            # === 阶段 2: 检测信号 ===
            events = detector.detect(current_snap)

            if events:
                now = datetime.now().strftime("%H:%M:%S")
                for e in events:
                    _print_event(now, e)

                # === 阶段 3: 执行跟单 ===
                if not mgr.connect_follower():
                    print(f"  [{cycle}] {R}连接跟单终端失败{W}")
                    continue

                results = executor.execute(
                    events,
                    order_sender=mgr.send_order,
                    symbol_info_provider=mgr.get_symbol_info,
                )
                mgr.disconnect()

                # 打印结果
                for r in results:
                    if r.status == ResultStatus.SUCCESS:
                        print(f"    {C}✓{W} {r.action} master={r.master_ticket} → follower={r.follower_ticket}")
                    elif r.status == ResultStatus.SKIPPED:
                        print(f"    {Y}⊘{W} {r.action} master={r.master_ticket}: {r.error}")
                    else:
                        print(f"    {R}✗{W} {r.action} master={r.master_ticket}: {r.error}")

                # 显示当前映射
                all_maps = store.get_all()
                if all_maps:
                    print(f"    当前映射: {len(all_maps)} 条 — ", end="")
                    for m in all_maps:
                        print(f"[{m['master_ticket']}→{m['follower_ticket']}] ", end="")
                    print()

            # 心跳
            if cycle % 10 == 0:
                sys.stdout.write(f"\r  轮询 {cycle} 次 | 持仓 {len(current_snap)} 个 | {datetime.now().strftime('%H:%M:%S')}")
                sys.stdout.flush()

            time.sleep(POLL_INTERVAL_S)

    except KeyboardInterrupt:
        print(f"\n\n  {Y}跟单循环已停止{W}")
        print(f"  共轮询 {cycle} 次")
        mgr.disconnect()


def _positions_to_snapshot(positions: list) -> dict[int, PositionSnapshot]:
    """MT5 positions → PositionSnapshot"""
    snap = {}
    if positions:
        for p in positions:
            snap[p.ticket] = PositionSnapshot(
                ticket=p.ticket,
                symbol=p.symbol,
                type=p.type,
                volume=p.volume,
                open_price=p.price_open,
                sl=p.sl,
                tp=p.tp,
                comment=p.comment,
                open_time=p.time,
            )
    return snap


def _print_event(now: str, event):
    """打印信号事件"""
    if event.event_type == EventType.OPEN:
        ns = event.new_state
        d = "买" if ns.type == 0 else "卖"
        print(f"\n  [{now}] {C}OPEN{W}  ticket={event.ticket} {ns.symbol} {d} 手数={ns.volume}")
    elif event.event_type == EventType.CLOSE:
        os = event.old_state
        print(f"\n  [{now}] {R}CLOSE{W} ticket={event.ticket} {os.symbol} 手数={os.volume}")
    elif event.event_type == EventType.MODIFY:
        ns, old = event.new_state, event.old_state
        changes = []
        if old.sl != ns.sl:
            changes.append(f"SL {old.sl}→{ns.sl}")
        if old.tp != ns.tp:
            changes.append(f"TP {old.tp}→{ns.tp}")
        print(f"\n  [{now}] {Y}MODIFY{W} ticket={event.ticket} {', '.join(changes)}")


if __name__ == "__main__":
    main()
