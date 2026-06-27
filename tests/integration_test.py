"""
跟单系统集成测试脚本 — 连接真实 MT5 终端验证完整链路。

⚠️ 重要安全规则:
  - 信号源 60218148 有真实资金，绝对只读，永不下单
  - 跟单目标 711621 是模拟账户，可以安全下单测试
  - 测试按顺序执行，每步有明确提示

使用方式:
  python tests/integration_test.py

测试步骤:
  Step 1: 信号源账户只读验证 (60218148)
  Step 2: 信号检测器实时监控
  Step 3: 跟单执行器验证 (需切换到 711621)
"""

import sys
import os
import time
import json
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import MetaTrader5 as mt5
from src.mt5_connector import MT5Connector
from src.signal_detector import SignalDetector, PositionSnapshot
from src.copy_executor import (
    CopyConfig, CopyExecutor, CopyResult,
    OrderMappingStore, ResultStatus,
)
from src.risk_manager import RiskManager, RiskConfig


# ================================================================
# 配置
# ================================================================

SIGNAL_SOURCE = 60218148   # 信号源（只读！真实资金）
FOLLOWER = 711621           # 跟单目标（模拟账户，可下单）

COLOR_GREEN = "\033[92m"
COLOR_RED = "\033[91m"
COLOR_YELLOW = "\033[93m"
COLOR_RESET = "\033[0m"


def print_header(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def print_ok(msg: str):
    print(f"  {COLOR_GREEN}✓{COLOR_RESET} {msg}")


def print_err(msg: str):
    print(f"  {COLOR_RED}✗{COLOR_RESET} {msg}")


def print_warn(msg: str):
    print(f"  {COLOR_YELLOW}⚠{COLOR_RESET} {msg}")


def ask_continue(prompt: str) -> bool:
    """询问用户是否继续"""
    ans = input(f"  {COLOR_YELLOW}?{COLOR_RESET} {prompt} [y/N]: ")
    return ans.lower() == "y"


# ================================================================
# Step 1: 验证当前登录的账户
# ================================================================

def step1_check_account():
    """
    检查当前 MT5 终端登录的是哪个账户。

    如果当前登录的是信号源 60218148:
      - 执行只读测试：获取账户、持仓、品种、行情
      - 验证连接稳定性

    如果当前登录的是跟单目标 711621:
      - 执行读+写测试（会实际下单！需确认）
    """
    print_header("Step 1: MT5 账户验证")

    conn = MT5Connector()
    if not conn.connected:
        print_err("MT5 连接失败 — 请确认 MT5 终端已运行并已登录")
        return None

    acc = conn.get_account_info()
    if acc is None:
        print_err("无法获取账户信息")
        conn.shutdown()
        return None

    trade_mode = "模拟" if acc.trade_mode == 0 else "真实" if acc.trade_mode == 1 else "其他"
    print(f"  账号: {acc.login}")
    print(f"  服务器: {acc.server}")
    print(f"  余额: {acc.balance:.2f} {acc.currency}")
    print(f"  类型: {trade_mode}")

    if acc.login == SIGNAL_SOURCE:
        print_ok(f"当前是信号源账户 {SIGNAL_SOURCE} — 仅执行只读测试")
        step1_readonly_tests(conn)
    elif acc.login == FOLLOWER:
        print_warn(f"当前是跟单账户 {FOLLOWER} — 可执行下单测试")
        step1_readonly_tests(conn)
    else:
        print_warn(f"未知账户 {acc.login}，预期信号源 {SIGNAL_SOURCE} 或跟单 {FOLLOWER}")

    conn.shutdown()
    return acc


def step1_readonly_tests(conn: MT5Connector):
    """只读测试 — 对任何账户都安全"""
    print()
    print("  --- 只读测试 ---")

    # 品种列表
    symbols = conn.get_symbols()
    print_ok(f"品种总数: {len(symbols)}")

    # EURUSD 行情
    eurusd = None
    for s in symbols:
        if s.name.startswith("EURUSD"):
            eurusd = s.name
            break

    if eurusd:
        tick = conn.get_symbol_tick(eurusd)
        if tick:
            print_ok(f"{eurusd}: bid={tick.bid}, ask={tick.ask}")
        else:
            print_err(f"无法获取 {eurusd} 报价")
    else:
        print_warn("未找到 EURUSD 品种")

    # 持仓
    positions = conn.get_positions()
    if positions:
        print(f"  当前持仓: {len(positions)} 个")
        for p in positions:
            dt = datetime.fromtimestamp(p.time)
            print(f"    Ticket={p.ticket} {p.symbol} {'买' if p.type == 0 else '卖'} "
                  f"手数={p.volume} 盈亏={p.profit:.2f} [{dt}]")
    else:
        print("  当前持仓: 0")


# ================================================================
# Step 2: 信号检测器实时监控（在信号源上运行）
# ================================================================

def step2_monitor_signals():
    """
    在信号源 60218148 上启动信号检测器监控。

    此测试模拟真实跟单场景:
      1. 建立基准快照
      2. 持续轮询（每 2 秒一次）
      3. 检测到变化时打印信号

    运行期间，外部信号触发交易后你能看到实时检测结果。
    按 Ctrl+C 停止。
    """
    print_header("Step 2: 信号检测器实时监控")
    print_warn("此测试在信号源 60218148 上运行，只读，不会下单")
    print()
    print("  监控期间，如果外部信号触发交易，你将在下方看到:")
    print("    OPEN   — 新开仓检测")
    print("    CLOSE  — 平仓检测")
    print("    MODIFY — SL/TP 修改检测")
    print()

    if not ask_continue("开始监控？"):
        return

    import MetaTrader5 as mt5_local
    if not mt5_local.initialize():
        print_err(f"MT5 初始化失败: {mt5_local.last_error()}")
        return

    acc = mt5_local.account_info()
    if acc.login != SIGNAL_SOURCE:
        print_err(f"当前账户是 {acc.login}，不是信号源 {SIGNAL_SOURCE}！")
        print_err("请在 MT5 中切换到信号源账户后重试")
        mt5_local.shutdown()
        return

    detector = SignalDetector()
    poll_count = 0
    print(f"\n  开始监控... (按 Ctrl+C 停止)")
    print(f"  时间: {datetime.now().strftime('%H:%M:%S')}")

    try:
        while True:
            poll_count += 1
            # 获取当前持仓快照
            positions = mt5_local.positions_get()
            current_snap = {}
            if positions:
                for p in positions:
                    current_snap[p.ticket] = PositionSnapshot(
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

            # 检测变化
            events = detector.detect(current_snap)

            if events:
                now = datetime.now().strftime('%H:%M:%S')
                for e in events:
                    if e.event_type.name == "OPEN":
                        ns = e.new_state
                        direction = "买" if ns.type == 0 else "卖"
                        print(f"\n  [{now}] {COLOR_GREEN}OPEN{COLOR_RESET}  "
                              f"Ticket={e.ticket} {ns.symbol} {direction} "
                              f"手数={ns.volume} 开仓价={ns.open_price}")
                    elif e.event_type.name == "CLOSE":
                        os = e.old_state
                        direction = "买" if os.type == 0 else "卖"
                        print(f"\n  [{now}] {COLOR_RED}CLOSE{COLOR_RESET} "
                              f"Ticket={e.ticket} {os.symbol} {direction} "
                              f"手数={os.volume}")
                    elif e.event_type.name == "MODIFY":
                        ns = e.new_state
                        old_s = e.old_state
                        changes = []
                        if old_s.sl != ns.sl:
                            changes.append(f"SL {old_s.sl}→{ns.sl}")
                        if old_s.tp != ns.tp:
                            changes.append(f"TP {old_s.tp}→{ns.tp}")
                        if old_s.volume != ns.volume:
                            changes.append(f"手数 {old_s.volume}→{ns.volume}")
                        print(f"\n  [{now}] {COLOR_YELLOW}MODIFY{COLOR_RESET} "
                              f"Ticket={e.ticket} {', '.join(changes)}")

            # 每 10 次轮询打印心跳
            if poll_count % 10 == 0:
                sys.stdout.write(f"\r  轮询 {poll_count} 次, "
                                 f"持仓 {len(current_snap)} 个...")
                sys.stdout.flush()

            time.sleep(2.0)  # 2 秒轮询间隔

    except KeyboardInterrupt:
        print(f"\n\n  监控停止。共轮询 {poll_count} 次")
    finally:
        mt5_local.shutdown()


# ================================================================
# Step 3: 跟单执行器测试（需切换到 711621）
# ================================================================

def step3_executor_smoke_test():
    """
    在跟单账户 711621 上验证下单逻辑。

    注意：此步骤会实际下单！请确认 MT5 已切换到模拟账户 711621。
    """
    print_header("Step 3: 跟单执行器冒烟测试")
    print_warn("此步骤会在跟单账户 711621 上实际下单！")
    print_warn("请确认 MT5 终端已切换到模拟账户 711621")
    print()

    if not ask_continue("已切换到 711621，开始冒烟测试？"):
        return

    # 初始化 MT5
    import MetaTrader5 as mt5_local
    if not mt5_local.initialize():
        print_err(f"MT5 初始化失败: {mt5_local.last_error()}")
        return

    acc = mt5_local.account_info()
    if acc.login != FOLLOWER:
        print_err(f"当前账户是 {acc.login}，不是跟单账户 {FOLLOWER}！")
        mt5_local.shutdown()
        return

    print_ok(f"已连接跟单账户 {FOLLOWER}")

    # 1. 测试品种映射
    print("\n  --- 测试 1: 品种映射 ---")
    symbols = mt5_local.symbols_get()
    symbol_names = {s.name for s in symbols}
    # 找到带 _ 后缀的 EURUSD
    eurusd_mapped = None
    for name in symbol_names:
        if name.startswith("EURUSD"):
            eurusd_mapped = name
            break
    if eurusd_mapped:
        print_ok(f"跟单账户 EURUSD 品种: {eurusd_mapped}")
    else:
        print_err("未找到 EURUSD 品种")
        mt5_local.shutdown()
        return

    # 2. 测试获取品种规格
    print("\n  --- 测试 2: 品种规格 ---")
    sym_info = mt5_local.symbol_info(eurusd_mapped)
    if sym_info:
        print(f"    volume_step={sym_info.volume_step}")
        print(f"    volume_min={sym_info.volume_min}")
        print(f"    volume_max={sym_info.volume_max}")
        print(f"    digits={sym_info.digits}")
        print_ok(f"品种规格获取正常")
    else:
        print_err("无法获取品种信息")

    # 3. 测试构建下单请求（不发送）
    print("\n  --- 测试 3: 下单请求结构 ---")
    request = {
        "action": mt5_local.TRADE_ACTION_DEAL,
        "symbol": eurusd_mapped,
        "volume": 0.01,
        "type": mt5_local.ORDER_TYPE_BUY,
        "price": 0.0,
        "deviation": 10,
        "magic": 888000,
        "comment": "integration_test_verify_only",
        "type_time": mt5_local.ORDER_TIME_GTC,
        "type_filling": mt5_local.ORDER_FILLING_IOC,
    }
    # 用 ORDER_CHECK 验证而不实际下单
    check_result = mt5_local.order_check(request)
    if check_result:
        print_ok(f"订单检查通过 (retcode={check_result.retcode})")
        print(f"    comment={check_result.comment}")
    else:
        print_err(f"订单检查失败: {mt5_local.last_error()}")

    # 4. 可选：实际下单 0.01 手测试
    print()
    if ask_continue("发送 0.01 手 EURUSD 市价单 + 立即平仓？（验证完整下单链路）"):
        print("\n  发送市价买单 0.01 手...")
        result = mt5_local.order_send(request)
        if result and result.retcode == 10009:
            open_ticket = result.order
            print_ok(f"开仓成功! Ticket={open_ticket}, 价格={result.price}")

            # 立即平仓
            time.sleep(1)
            close_request = {
                "action": mt5_local.TRADE_ACTION_DEAL,
                "symbol": eurusd_mapped,
                "volume": 0.01,
                "type": mt5_local.ORDER_TYPE_SELL,
                "position": open_ticket,
                "price": 0.0,
                "deviation": 10,
                "magic": 888000,
                "comment": "integration_test_close",
                "type_time": mt5_local.ORDER_TIME_GTC,
                "type_filling": mt5_local.ORDER_FILLING_IOC,
            }
            close_result = mt5_local.order_send(close_request)
            if close_result and close_result.retcode == 10009:
                print_ok(f"平仓成功! 盈亏={close_result.profit:.2f}")
            else:
                print_err(f"平仓失败: retcode={close_result.retcode if close_result else mt5_local.last_error()}")
        else:
            print_err(f"开仓失败: retcode={result.retcode if result else mt5_local.last_error()}")
    else:
        print("  跳过实际下单测试")

    mt5_local.shutdown()


# ================================================================
# Step 4: 端到端模拟测试（不依赖真实下单）
# ================================================================

def step4_e2e_simulation():
    """
    使用模拟数据验证完整链路: 检测 → 风控 → 执行 → 对账。

    不需要真实 MT5 连接。
    """
    print_header("Step 4: 端到端模拟测试（无需 MT5）")

    from src.signal_detector import EventType, SignalEvent
    from src.copy_executor import CopyConfig, CopyExecutor, OrderMappingStore, ResultStatus
    from src.risk_manager import RiskManager, RiskConfig, RiskStatus
    from src.reconciler import Reconciler, ReconcileConfig, PositionInfo
    from src.orchestrator import Orchestrator
    from unittest.mock import MagicMock

    print("  模拟场景: 外部信号触发 EURUSD 0.10 手买入 → 系统跟单 → 验证结果")

    # 构建组件
    config = CopyConfig(
        ratio=1.0,
        symbol_mapping={"EURUSD": "EURUSD_"},
        min_order_interval_ms=100,  # 加快测试
    )
    store = OrderMappingStore()
    risk_config = RiskConfig(max_positions=10, max_position_pct=80.0)
    risk = RiskManager(risk_config)

    # Mock 组件
    detector = MagicMock()
    executor = CopyExecutor(config, store)
    reconciler = Reconciler(ReconcileConfig(auto_fix=False))

    # Mock 下单回调
    order_counter = [90000]
    captured_orders = []
    def mock_sender(req):
        order_counter[0] += 1
        captured_orders.append(req)
        mock_result = MagicMock()
        mock_result.retcode = 10009
        mock_result.order = order_counter[0]
        return 10009, mock_result

    orch = Orchestrator(
        signal_detector=MagicMock(),
        copy_executor=executor,
        risk_manager=risk,
        reconciler=reconciler,
        order_sender=mock_sender,
        poll_interval_ms=100,
    )
    orch.state = type(orch).__dict__['state'].__class__.RUNNING  # 绕过 enum

    # 模拟：外部信号触发 EURUSD 0.10 买
    event = SignalEvent(
        event_type=EventType.OPEN,
        ticket=10001,
        new_state=PositionSnapshot(
            ticket=10001, symbol="EURUSD", type=0, volume=0.10,
            open_price=1.08500, sl=1.0800, tp=1.0900,
        ),
    )
    detector = MagicMock()
    detector.detect.return_value = [event]
    detector._previous_snapshot = {}
    orch.detector = detector

    # 执行
    risk.check_all.return_value = type(
        "R", (), {"status": RiskStatus.PASSED, "reason": ""}
    )()
    results = orch._process_cycle(lambda: {}, lambda s: {"volume_step": 0.01, "volume_min": 0.01, "volume_max": 50.0})

    # 验证
    if len(results) == 1 and results[0].status == ResultStatus.SUCCESS:
        print_ok(f"跟单成功: master_ticket=10001 → follower_ticket={results[0].follower_ticket}")
        print_ok(f"下单请求: symbol={captured_orders[0]['symbol']}, volume={captured_orders[0]['volume']}")
        print_ok(f"映射已记录: {store.get(10001) is not None}")
    else:
        print_err(f"跟单失败: {results}")

    # 模拟平仓
    close_event = SignalEvent(
        event_type=EventType.CLOSE, ticket=10001,
        old_state=PositionSnapshot(ticket=10001, symbol="EURUSD", type=0, volume=0.10),
    )
    detector.detect.return_value = [close_event]
    results = orch._process_cycle(lambda: {}, lambda s: {"volume_step": 0.01})

    if len(results) == 1 and results[0].status == ResultStatus.SUCCESS:
        print_ok(f"平仓成功: master_ticket=10001")
        print_ok(f"映射已清除: {store.get(10001) is None}")
    else:
        print_err(f"平仓失败: {results}")

    # 对账验证
    lead_positions = {}
    follower_positions = []
    mapping = store.get_all_as_dict()
    reconcile_results = reconciler.reconcile(lead_positions, follower_positions, mapping, ratio=1.0)
    if len(reconcile_results) == 0:
        print_ok("对账通过: 主从持仓一致")
    else:
        summary = reconciler.summarize(reconcile_results)
        print_err(f"对账差异: {summary['total_diffs']} 条")


# ================================================================
# 主菜单
# ================================================================

def main():
    print("""
╔══════════════════════════════════════════════════════════╗
║       MT 跟单系统 — 集成测试                              ║
║                                                          ║
║  信号源: 60218148 (只读！)                                ║
║  跟单目标: 711621 (模拟)                                  ║
╚══════════════════════════════════════════════════════════╝
""")

    while True:
        print("""
请选择测试:
  1. 验证当前账户 (只读，检测连接和行情)
  2. 信号检测器实时监控 (需登录 60218148)
  3. 跟单执行器冒烟测试 (需登录 711621，会实际下单!)
  4. 端到端模拟测试 (无需 MT5 连接，纯逻辑验证)
  5. 运行全部单元测试
  0. 退出
""")
        choice = input("  输入选项 [0-5]: ").strip()

        if choice == "1":
            step1_check_account()
        elif choice == "2":
            step2_monitor_signals()
        elif choice == "3":
            step3_executor_smoke_test()
        elif choice == "4":
            step4_e2e_simulation()
        elif choice == "5":
            print_header("运行全部单元测试")
            os.system("python -m pytest tests/ -v")
        elif choice == "0":
            print("  退出")
            break
        else:
            print_err("无效选项")


if __name__ == "__main__":
    main()
