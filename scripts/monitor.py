"""
信号监控进程 — 常连信号源 MT5 终端，实时检测持仓变化并广播。

运行方式:
  python scripts/monitor.py

职责:
  - 常连信号源 MT5 终端 (711591)
  - 每 200ms 轮询持仓
  - 快照差量检测 → 生成信号事件
  - ZeroMQ PUB 广播到 :5555
  - 每 5s 发送心跳
"""

import sys
import os
import time
import signal
import logging

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import zmq
import MetaTrader5 as mt5
from src.signal_detector import (
    SignalDetector, PositionSnapshot, EventType,
)
from src.ipc import pack_event, pack_heartbeat, pack_stop

# ---- 配置 ----
LEAD_PATH = os.environ.get(
    "LEAD_OVERRIDE_PATH",
    r"C:\Program Files\MetaTrader 5\terminal64.exe"
)
POLL_INTERVAL_MS = 50   # 轮询间隔（毫秒），端到端同步延迟 ~100ms
HEARTBEAT_INTERVAL_S = 5

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [MONITOR] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("monitor")


def main():
    logger.info("=== 信号监控进程启动 ===")

    # ---- 连接 MT5 信号源 ----
    logger.info(f"连接信号源终端: {LEAD_PATH}")
    if not mt5.initialize(path=LEAD_PATH):
        logger.error(f"MT5 初始化失败: {mt5.last_error()}")
        return 1

    acc = mt5.account_info()
    if acc is None:
        logger.error("无法获取账户信息")
        mt5.shutdown()
        return 1

    logger.info(f"已连接: 账号={acc.login} 服务器={acc.server} "
                f"余额={acc.balance:.2f} {acc.currency}")

    # ---- 初始化 ZMQ PUB ----
    ctx = zmq.Context()
    pub = ctx.socket(zmq.PUB)
    pub.bind(f"tcp://127.0.0.1:5555")
    logger.info("ZMQ PUB 已绑定 tcp://127.0.0.1:5555")

    # ---- 初始化信号检测器 ----
    detector = SignalDetector()
    poll_count = 0
    signal_count = 0
    last_heartbeat = time.time()
    running = True

    def shutdown(sig=None, frame=None):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # ---- 主循环 ----
    logger.info(f"开始监控 (轮询间隔={POLL_INTERVAL_MS}ms)...")
    t_start = time.time()

    try:
        while running:
            try:
                # 获取当前持仓
                positions = mt5.positions_get()
                current_snap = {}
                if positions:
                    for p in positions:
                        current_snap[p.ticket] = PositionSnapshot(
                            ticket=p.ticket, symbol=p.symbol,
                            type=p.type, volume=p.volume,
                            open_price=p.price_open,
                            sl=p.sl, tp=p.tp,
                            comment=p.comment, open_time=p.time,
                        )

                # 检测变化
                events = detector.detect(current_snap)

                # 广播事件
                for evt in events:
                    if evt.event_type == EventType.OPEN:
                        ns = evt.new_state
                        msg = pack_event(
                            "OPEN", evt.ticket, ns.symbol,
                            direction=ns.type, volume=ns.volume,
                            open_price=ns.open_price, sl=ns.sl, tp=ns.tp,
                        )
                        direction = "买" if ns.type == 0 else "卖"
                        logger.info(f"→ 广播 OPEN: ticket={evt.ticket} "
                                    f"{ns.symbol} {direction} 手数={ns.volume}")

                    elif evt.event_type == EventType.CLOSE:
                        os = evt.old_state
                        msg = pack_event(
                            "CLOSE", evt.ticket, os.symbol,
                            direction=os.type, volume=os.volume,
                        )
                        logger.info(f"→ 广播 CLOSE: ticket={evt.ticket} "
                                    f"{os.symbol}")

                    elif evt.event_type == EventType.MODIFY:
                        ns, old = evt.new_state, evt.old_state
                        msg = pack_event(
                            "MODIFY", evt.ticket, ns.symbol,
                            old_sl=old.sl, new_sl=ns.sl,
                            old_tp=old.tp, new_tp=ns.tp,
                            old_volume=old.volume, new_volume=ns.volume,
                        )
                        logger.info(f"→ 广播 MODIFY: ticket={evt.ticket}")

                    pub.send(msg)
                    signal_count += 1

                poll_count += 1

                # 心跳
                now = time.time()
                if now - last_heartbeat >= HEARTBEAT_INTERVAL_S:
                    pub.send(pack_heartbeat())
                    last_heartbeat = now
                    if poll_count % 50 == 0:
                        elapsed = now - t_start
                        logger.debug(f"心跳: {poll_count} 轮, "
                                     f"{signal_count} 信号, "
                                     f"运行 {elapsed:.0f}s")

                # 轮询间隔
                time.sleep(POLL_INTERVAL_MS / 1000.0)

            except Exception as e:
                logger.error(f"轮询异常: {e}", exc_info=True)
                time.sleep(1.0)  # 出错后等 1 秒再试

    finally:
        logger.info("正在停止...")
        pub.send(pack_stop())
        time.sleep(0.1)
        pub.close()
        ctx.term()
        mt5.shutdown()
        logger.info(f"监控进程已停止 (共 {poll_count} 轮, {signal_count} 信号)")


if __name__ == "__main__":
    sys.exit(main() or 0)
