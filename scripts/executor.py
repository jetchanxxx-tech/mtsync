"""
跟单执行进程 — 常连跟单目标 MT5 终端，接收信号并立即执行。

运行方式:
  python scripts/executor.py

职责:
  - 常连跟单目标 MT5 终端 (711621)
  - ZeroMQ SUB 订阅 :5555 接收信号
  - 立即执行跟单操作（开仓/平仓/修改）
  - 支持 1:N：每个从账户启动一个 executor 进程
"""

import sys
import os
import time
import signal
import logging

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import zmq
import MetaTrader5 as mt5
from src.signal_detector import EventType, SignalEvent, PositionSnapshot
from src.copy_executor import (
    CopyConfig, CopyExecutor, CopyResult,
    OrderMappingStore, ResultStatus,
)
from src.risk_manager import RiskManager, RiskConfig
from src.ipc import unpack_event

# ---- 配置 ----
FOLLOWER_PATH = os.environ.get(
    "FOLLOWER_OVERRIDE_PATH",
    r"D:\MetaTrader 5\terminal64.exe"
)
MONITOR_PUB_ADDR = "tcp://127.0.0.1:5555"
MAGIC = 888000

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [EXECUTOR] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("executor")


def main():
    logger.info("=== 跟单执行进程启动 ===")

    # ---- 连接 MT5 跟单终端 ----
    logger.info(f"连接跟单终端: {FOLLOWER_PATH}")
    if not mt5.initialize(path=FOLLOWER_PATH):
        logger.error(f"MT5 初始化失败: {mt5.last_error()}")
        return 1

    acc = mt5.account_info()
    if acc is None:
        logger.error("无法获取账户信息")
        mt5.shutdown()
        return 1

    logger.info(f"已连接: 账号={acc.login} 服务器={acc.server} "
                f"余额={acc.balance:.2f} {acc.currency}")

    # ---- 初始化跟单组件 ----
    config = CopyConfig(
        ratio=1.0,
        symbol_mapping={},  # 同一经纪商，无需映射
        magic=MAGIC,
        min_order_interval_ms=500,
    )
    store = OrderMappingStore()
    executor = CopyExecutor(config, store)
    risk = RiskManager(RiskConfig(
        max_positions=10,
        max_position_pct=80.0,
    ))

    # ---- 初始化 ZMQ SUB ----
    ctx = zmq.Context()
    sub = ctx.socket(zmq.SUB)
    sub.connect(MONITOR_PUB_ADDR)
    sub.setsockopt_string(zmq.SUBSCRIBE, "")  # 订阅所有消息
    logger.info(f"ZMQ SUB 已连接 {MONITOR_PUB_ADDR}")

    # ---- 主循环 ----
    running = True
    execute_count = 0

    def shutdown(sig=None, frame=None):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    logger.info("等待信号...")
    logger.info(f"{'='*50}")

    try:
        while running:
            try:
                # 非阻塞接收（1 秒超时）
                if sub.poll(1000) == 0:
                    continue

                data = sub.recv()
                msg = unpack_event(data)

                msg_type = msg["type"]

                if msg_type == "HEARTBEAT":
                    continue  # 心跳忽略

                if msg_type == "STOP":
                    logger.info("收到停止信号")
                    break

                # 构建 SignalEvent
                event = _build_signal_event(msg)
                if event is None:
                    logger.warning(f"无法解析消息: {msg}")
                    continue

                # 风控检查（仅开仓需要）
                if event.event_type == EventType.OPEN:
                    ns = event.new_state
                    positions = mt5.positions_get()
                    pos_count = len(positions) if positions else 0
                    risk_result = risk.check_all(
                        current_positions=pos_count,
                        symbol=ns.symbol,
                        new_volume=ns.volume,
                    )
                    if risk_result.status.name == "REJECTED":
                        logger.warning(
                            f"风控拒绝 ticket={event.ticket}: "
                            f"{risk_result.reason}"
                        )
                        continue

                # 执行跟单
                def order_sender(req):
                    result = mt5.order_send(req)
                    if result is None:
                        err = mt5.last_error()
                        logger.error(f"下单失败: {err}")
                        return -1, err
                    return result.retcode, result

                results = executor.execute(
                    [event],
                    order_sender=order_sender,
                    symbol_info_provider=mt5.symbol_info,
                )

                t_done = time.time()
                msg_latency_ms = (t_done - msg["timestamp"]) * 1000

                for r in results:
                    if r.status == ResultStatus.SUCCESS:
                        execute_count += 1
                        logger.info(
                            f"LATENCY={msg_latency_ms:.0f}ms | "
                            f"{r.action} master={r.master_ticket} "
                            f"-> follower={r.follower_ticket}"
                        )
                    elif r.status == ResultStatus.SKIPPED:
                        logger.warning(
                            f"LATENCY={msg_latency_ms:.0f}ms | "
                            f"SKIP {r.action} master={r.master_ticket}: "
                            f"{r.error}"
                        )
                    else:
                        logger.error(
                            f"LATENCY={msg_latency_ms:.0f}ms | "
                            f"FAIL {r.action} master={r.master_ticket}: "
                            f"{r.error}"
                        )

            except zmq.ZMQError as e:
                logger.error(f"ZMQ 错误: {e}")
            except Exception as e:
                logger.error(f"执行异常: {e}", exc_info=True)

    finally:
        logger.info("正在停止...")
        sub.close()
        ctx.term()
        mt5.shutdown()
        logger.info(f"执行进程已停止 (共执行 {execute_count} 次)")


def _build_signal_event(msg: dict) -> SignalEvent | None:
    """从 IPC 消息重建 SignalEvent"""
    msg_type = msg["type"]
    ticket = msg["ticket"]
    symbol = msg["symbol"]

    if msg_type == "OPEN":
        event_type = EventType.OPEN
        old_state = None
        new_state = PositionSnapshot(
            ticket=ticket, symbol=symbol,
            type=msg["direction"], volume=msg["volume"],
            open_price=msg.get("open_price", 0.0),
            sl=msg.get("sl", 0.0), tp=msg.get("tp", 0.0),
        )
    elif msg_type == "CLOSE":
        event_type = EventType.CLOSE
        old_state = PositionSnapshot(
            ticket=ticket, symbol=symbol,
            type=msg["direction"], volume=msg["volume"],
        )
        new_state = None
    elif msg_type == "MODIFY":
        event_type = EventType.MODIFY
        old_state = PositionSnapshot(
            ticket=ticket, symbol=symbol,
            type=msg.get("direction", 0),
            volume=msg.get("old_volume", 0.0),
            sl=msg.get("old_sl", 0.0), tp=msg.get("old_tp", 0.0),
        )
        new_state = PositionSnapshot(
            ticket=ticket, symbol=symbol,
            type=msg.get("direction", 0),
            volume=msg.get("new_volume", 0.0),
            sl=msg.get("new_sl", 0.0), tp=msg.get("new_tp", 0.0),
        )
    else:
        return None

    return SignalEvent(
        event_type=event_type,
        ticket=ticket,
        old_state=old_state,
        new_state=new_state,
        timestamp=msg["timestamp"],
    )


if __name__ == "__main__":
    sys.exit(main() or 0)
