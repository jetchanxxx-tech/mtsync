"""
进程间通讯层 — ZeroMQ PUB/SUB 模式。

架构:
  monitor.py (PUB :5555) ──→ executor.py (SUB :5555)

  支持 1:N：多个 executor 进程订阅同一个 PUB 端口。

消息格式 (JSON):
  {
    "type": "OPEN" | "CLOSE" | "MODIFY" | "HEARTBEAT",
    "timestamp": 1700000000.123,
    "ticket": 10001,
    "symbol": "EURUSD",
    "direction": 0,         # 0=BUY, 1=SELL
    "volume": 0.10,
    "open_price": 1.08500,
    "sl": 1.0800,
    "tp": 1.0900,
    "old_sl": 1.0800,       # MODIFY only
    "new_sl": 1.0820,       # MODIFY only
    "old_tp": 1.0900,       # MODIFY only
    "new_tp": 1.0920,       # MODIFY only
    "old_volume": 0.10,     # partial close
    "new_volume": 0.05,     # partial close
  }
"""

import json
import time
from enum import Enum

# ZMQ 端口配置
MONITOR_PUB_PORT = 5555       # monitor 广播信号
EXECUTOR_REP_PORT = 5556      # executor 返回状态（预留）


class IPCMessageType(str, Enum):
    OPEN = "OPEN"
    CLOSE = "CLOSE"
    MODIFY = "MODIFY"
    HEARTBEAT = "HEARTBEAT"
    STOP = "STOP"


def pack_event(event_type: str, ticket: int, symbol: str,
               direction: int = 0, volume: float = 0.0,
               open_price: float = 0.0, sl: float = 0.0, tp: float = 0.0,
               old_sl: float = 0.0, new_sl: float = 0.0,
               old_tp: float = 0.0, new_tp: float = 0.0,
               old_volume: float = 0.0, new_volume: float = 0.0,
               ) -> bytes:
    """将事件打包为 JSON 字节串"""
    msg = {
        "type": event_type,
        "timestamp": time.time(),
        "ticket": ticket,
        "symbol": symbol,
        "direction": direction,
        "volume": volume,
        "open_price": open_price,
        "sl": sl,
        "tp": tp,
    }
    if event_type == "MODIFY":
        msg.update({
            "old_sl": old_sl, "new_sl": new_sl,
            "old_tp": old_tp, "new_tp": new_tp,
            "old_volume": old_volume, "new_volume": new_volume,
        })
    return json.dumps(msg).encode("utf-8")


def unpack_event(data: bytes) -> dict:
    """解包事件"""
    return json.loads(data.decode("utf-8"))


def pack_heartbeat() -> bytes:
    """打包心跳消息"""
    return json.dumps({
        "type": "HEARTBEAT",
        "timestamp": time.time(),
    }).encode("utf-8")


def pack_stop() -> bytes:
    """打包停止消息"""
    return json.dumps({
        "type": "STOP",
        "timestamp": time.time(),
    }).encode("utf-8")
