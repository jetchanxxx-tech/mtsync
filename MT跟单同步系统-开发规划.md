# MetaTrader 4/5 账户跟单同步系统 — 开发规划

> **目标**：在 MetaTrader 4/5 平台上实现 **1 对多跟单同步**（一个主账户的交易操作实时同步到多个从账户），覆盖 **全部订单类型**（市价单、限价单、止损单、挂单修改/删除）。
>
> **核心场景**：当主账户（Lead Trader）在 MT 终端开仓、平仓、修改止损止盈或挂单时，从账户（Followers）自动按比例执行相同的交易操作。
>
> **技术路线**：
> - **MT5** → 使用官方 `MetaTrader5` Python 库，轮询 + 事件驱动混合模式
> - **MT4** → MQL4 EA + ZeroMQ 桥接（Darwinex DWX 三通道模式）

---

## 目录

- [1. 先决条件](#1-先决条件)
- [2. 所需接口/数据](#2-所需接口数据)
- [3. 推荐技术栈](#3-推荐技术栈)
- [4. 系统架构](#4-系统架构)
- [5. 服务器架构](#5-服务器架构)
- [6. 核心模块详解](#6-核心模块详解)
- [7. MT4/MT5 差异处理](#7-mt4mt5-差异处理)
- [8. 关键挑战与应对](#8-关键挑战与应对)
- [9. 实施路线图](#9-实施路线图)
- [10. 目录结构建议](#10-目录结构建议)
- [11. 验证方案](#11-验证方案)
- [12. 级联跟单场景](#12-级联跟单场景)
- [13. 方案延迟对比](#13-方案延迟对比)
- [14. 大规模跟单管理方案](#14-大规模跟单管理方案)
- [15. MetaTrader Signals 评估](#15-metatrader-signals-评估)

---

## 1. 先决条件

### 1.1 MT 终端环境

| 条件 | MT5 | MT4 |
|---|---|---|
| **终端版本** | MetaTrader 5 (build ≥ 3800) | MetaTrader 4 (build ≥ 1400) |
| **账户类型** | 对冲账户（Hedging）或 净额账户（Netting） | 对冲账户（Hedging） |
| **运行模式** | 实盘 / 模拟盘均可 | 实盘 / 模拟盘均可 |
| **终端数量** | 主账户 1 个终端 + 每个从账户 1 个终端（或同一终端多账户） | 同左 |
| **EA 权限** | 主终端需启用 EA 交易 | 主终端 + 从终端均需启用 EA 交易 |
| **自动交易** | 终端 → 工具 → 选项 → 允许自动交易 | 同左 |
| **DLL 导入** | — | 需允许 DLL 导入（ZeroMQ 桥接依赖） |

### 1.2 基础设施

| 条件 | 说明 |
|---|---|
| **VPS/服务器** | Windows Server 2019+ 或 Windows 10/11 Pro，建议与经纪商服务器同区域 |
| **MT5 方案** | 仅需 Python 服务器 + MT 终端，无额外桥接组件 |
| **MT4 方案** | 需要 ZeroMQ DLL（`libzmq.dll` + `mt4zmq.dll`）部署到 MT4 目录 |
| **数据库** | PostgreSQL（账户映射、订单记录、跟单配置、审计日志） |
| **Redis** | 订单状态缓存、分布式锁、账户在线状态 |
| **消息队列** | Redis Streams（解耦信号接收和订单执行） |

### 1.3 技术与知识

- 熟悉 MQL4 / MQL5 语言（编写主账户端的信号捕获 EA）
- 熟悉 Python asyncio 异步编程
- 理解 MT 平台的订单系统：`POSITION` / `ORDER`（MT5）和 `OrderSend` / `OrderModify`（MT4）的差异
- 了解 ZeroMQ 通信模式（MT4 方案必需）：PUSH/PULL（命令）、PUB/SUB（数据流）
- 理解 MT 经纪商限制：最小下单间隔、最大同时持仓数、禁止高频交易

---

## 2. 所需接口/数据

### 2.1 主账户端 — 交易信号捕获

#### MT5 方案（Python `MetaTrader5` 库）

| 操作 | API 函数 | 用途 |
|---|---|---|
| 初始化连接 | `mt5.initialize(path=...)` | 连接指定 MT5 终端 |
| 获取持仓 | `mt5.positions_get(symbol="EURUSD")` | 轮询主账户当前持仓 |
| 获取订单 | `mt5.orders_get(symbol="EURUSD")` | 轮询挂单列表 |
| 获取成交历史 | `mt5.history_deals_get(...)` | 检测新成交（差量对比法） |
| 获取行情 | `mt5.symbol_info_tick("EURUSD")` | 获取实时报价 |
| 获取账户信息 | `mt5.account_info()` | 余额、净值、保证金 |

> **检测策略**：定时轮询（间隔 200ms-500ms），对比快照差量发现新开仓/平仓/修改操作。

#### MT4 方案（MQL4 EA + ZeroMQ 桥接）

| 通道 | 端口 | 方向 | 协议 | 用途 |
|---|---|---|---|---|
| **PUSH** | 32768 | Python → MT4 | REQ/REP | 命令通道（查询持仓、下单指令） |
| **PULL** | 32769 | MT4 → Python | PUSH/PULL | 响应通道（订单信息、执行结果） |
| **PUB** | 32770 | MT4 → Python | PUB/SUB | 数据流通道（实时报价、即时成交事件推送） |

```
Python 侧 (pyzmq):
  - REQ socket → 连接 PUSH 端口，发送查询/下单命令
  - PULL socket → 绑定 PULL 端口，接收 MT4 响应
  - SUB socket → 连接 PUB 端口，订阅实时数据

MT4 侧 (MQL4 EA + mql-zmq DLL):
  - REP socket → 绑定 PUSH 端口，接收命令
  - PUSH socket → 连接 PULL 端口，发送响应
  - PUB socket → 绑定 PUB 端口，广播行情和成交事件
```

### 2.2 从账户端 — 订单执行

#### MT5 方案

| 操作 | API 函数 | 说明 |
|---|---|---|
| 市价买入 | `mt5.order_send({action: DEAL, type: BUY, ...})` | 开多仓 |
| 市价卖出 | `mt5.order_send({action: DEAL, type: SELL, ...})` | 开空仓 |
| 平仓 | `mt5.order_send({action: DEAL, type: CLOSE, position: ticket})` | 按 ticket 平仓 |
| 挂限价单 | `mt5.order_send({action: PENDING, type: BUY_LIMIT, ...})` | LIMIT 挂单 |
| 挂止损单 | `mt5.order_send({action: PENDING, type: BUY_STOP, ...})` | STOP 挂单 |
| 修改持仓 | `mt5.order_send({action: MODIFY, position: ticket, sl: ..., tp: ...})` | 修改 SL/TP |
| 删挂单 | `mt5.order_send({action: REMOVE, order: ticket})` | 删除挂单 |
| 获取交易规则 | `mt5.symbol_info("EURUSD")` | 获取合约规格、精度 |

#### MT4 方案

通过 ZeroMQ 桥接向 MT4 终端发送标准化的订单指令（JSON 格式），EA 端调用 `OrderSend()` / `OrderClose()` / `OrderModify()` 执行。

### 2.3 订单映射数据结构

```python
# 主订单 → 从订单映射（存储在 PostgreSQL + Redis 热缓存）
order_mapping = {
    "master_ticket": 12345678,       # 主账户持仓/订单 ticket
    "master_account_id": "lead_01",  # 主账户标识
    "symbol": "EURUSD",              # 交易品种
    "direction": "BUY",              # 方向
    "master_volume": 0.10,           # 主账户手数
    "master_open_price": 1.0850,     # 主账户开仓价
    "master_sl": 1.0800,             # 主账户止损
    "master_tp": 1.0950,             # 主账户止盈
    "follower_positions": [          # 从账户对应持仓列表
        {
            "account_id": "follower_01",
            "follower_ticket": 87654321,
            "follower_volume": 0.05,     # 按比例计算后
            "follower_open_price": 1.0851,
            "status": "OPEN",
            "copy_ratio": 0.5,
        },
        # ...更多从账户
    ],
    "created_at": "2026-06-18T14:30:22Z",
    "updated_at": "2026-06-18T14:30:22Z",
}
```

---

## 3. 推荐技术栈

| 层级 | 技术选型 | 理由 |
|---|---|---|
| **语言** | Python 3.12+ | `MetaTrader5` 官方库 + `pyzmq` 桥接，生态成熟 |
| **MT5 交互** | `MetaTrader5` 官方 Python 库 | 直接调用 MT5 终端函数，无需额外 DLL |
| **MT4 交互** | MQL4 EA + ZeroMQ (`pyzmq` + `mql-zmq`) | 已验证的生产方案（Darwinex DWX），亚毫秒延迟 |
| **异步框架** | asyncio | 同时管理多个 MT 终端的连接和轮询 |
| **数据库** | PostgreSQL 16 | 订单映射、审计日志、账户配置 |
| **缓存** | Redis 7 | 订单快照缓存、轮询差量对比、分布式锁 |
| **消息队列** | Redis Streams | 解耦信号检测和订单执行 |
| **部署** | Docker（Linux 容器）+ Windows VPS（MT 终端） | 核心逻辑容器化，MT 终端独立运行 |
| **监控** | Prometheus + Grafana | 跟单延迟、成功率、MT 终端在线状态 |

---

## 4. 系统架构

### 4.1 架构全景图

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          MT 跟单同步系统                                  │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│   ┌──────────────────────────────┐                                      │
│   │     主账户 MT 终端            │                                      │
│   │  ┌────────────────────────┐  │                                      │
│   │  │ MT5: Python 直接轮询    │  │                                      │
│   │  │ MT4: 信号捕获 EA       │  │                                      │
│   │  │   (ZeroMQ PUB 推送)    │  │                                      │
│   │  └───────────┬────────────┘  │                                      │
│   └──────────────┼───────────────┘                                      │
│                  │ 交易信号 (JSON)                                       │
│                  ▼                                                       │
│   ┌──────────────────────────────────────────────┐                      │
│   │              信号检测与处理器                  │                      │
│   │  ┌────────────┐  ┌──────────┐  ┌──────────┐  │                      │
│   │  │ 轮询差量    │  │ 事件推送  │  │ 去重过滤  │  │                      │
│   │  │ (MT5)      │  │ (MT4)    │  │          │  │                      │
│   │  └────────────┘  └──────────┘  └──────────┘  │                      │
│   └──────────────────────┬───────────────────────┘                      │
│                          │                                              │
│                          ▼                                              │
│   ┌──────────────────────────────────────────────┐                      │
│   │              跟单核心引擎                      │                      │
│   │  ┌──────────┐  ┌───────────┐  ┌───────────┐  │                      │
│   │  │ 比例计算  │  │ 手数调整   │  │ 品种映射   │  │                      │
│   │  │          │  │(精度截断)  │  │(后缀处理)  │  │                      │
│   │  └──────────┘  └───────────┘  └───────────┘  │                      │
│   └──────────────────────┬───────────────────────┘                      │
│                          │                                              │
│                          ▼                                              │
│   ┌──────────────────────────────────────────────┐                      │
│   │              风控管理器                        │                      │
│   │  ┌──────────┐  ┌──────────┐  ┌────────────┐  │                      │
│   │  │ 仓位上限  │  │ 最大回撤  │  │ 下单间隔    │  │                      │
│   │  │          │  │          │  │ 控制 ≥500ms │  │                      │
│   │  └──────────┘  └──────────┘  └────────────┘  │                      │
│   └──────────────────────┬───────────────────────┘                      │
│                          │                                              │
│          ┌───────────────┼───────────────┐                              │
│          ▼               ▼               ▼                              │
│   ┌──────────┐    ┌──────────┐    ┌──────────┐                          │
│   │ 从账户    │    │ 从账户    │    │ 从账户    │   ...更多                │
│   │ MT 终端 1 │    │ MT 终端 2 │    │ MT 终端 3 │                          │
│   │           │    │           │    │           │                          │
│   │ MT5:      │    │ MT5:      │    │ MT4:      │                          │
│   │ mt5.      │    │ mt5.      │    │ ZeroMQ    │                          │
│   │ order_    │    │ order_    │    │ 桥接下单   │                          │
│   │ send()    │    │ send()    │    │           │                          │
│   └──────────┘    └──────────┘    └──────────┘                          │
│                                                                          │
│   ┌──────────────────────────────────────────────────────┐              │
│   │               状态管理与对账模块                       │              │
│   │  · 订单生命周期追踪                                   │              │
│   │  · 定期对账（主账户 vs 从账户持仓手数 + 盈亏）         │              │
│   │  · 差异自动修复 / 告警                                │              │
│   └──────────────────────────────────────────────────────┘              │
│                                                                          │
│   ┌──────────────────────────────────────────────────────┐              │
│   │       PostgreSQL          │         Redis            │              │
│   │  · 账户/跟单配置          │  · 持仓快照缓存            │              │
│   │  · 订单映射表             │  · 轮询差量对比            │              │
│   │  · 审计日志               │  · 分布式锁               │              │
│   │  · 品种规格缓存            │  · MT 终端心跳状态         │              │
│   └───────────────────────────┴──────────────────────────┘              │
└─────────────────────────────────────────────────────────────────────────┘
```

### 4.2 两种检测模式对比

| 维度 | MT5 轮询模式 | MT4 ZeroMQ 推送模式 |
|---|---|---|
| **检测延迟** | 200ms-500ms（取决于轮询间隔） | < 10ms（事件即时推送） |
| **CPU 开销** | 较高（持续轮询） | 低（事件驱动） |
| **实现复杂度** | 低（纯 Python） | 中（需部署 EA + DLL） |
| **丢失风险** | 两次轮询之间发生的快速交易可能合并 | 几乎无丢失（实时推送） |
| **与经纪商关系** | 无感知（标准 API 调用） | 需终端允许 DLL 导入 |
| **适用场景** | 中低频跟单（≥1分钟 K 线策略） | 高频/实时跟单 |

---

## 5. 服务器架构

### 5.1 部署拓扑

```
┌──────────────────────────────────────────────────────────────────┐
│                    VPS / 物理服务器 (Windows)                      │
│                                                                   │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │               MT 终端层（每个终端独立运行）                   │  │
│  │                                                             │  │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │  │
│  │  │ 主账户    │  │ 从账户 1  │  │ 从账户 2  │  │ 从账户 N  │   │  │
│  │  │ MT 终端  │  │ MT 终端  │  │ MT 终端  │  │ MT 终端  │   │  │
│  │  │          │  │          │  │          │  │          │   │  │
│  │  │ (MT4/5)  │  │ (MT4/5)  │  │ (MT4/5)  │  │ (MT4/5)  │   │  │
│  │  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘   │  │
│  │       │             │             │             │          │  │
│  └───────┼─────────────┼─────────────┼─────────────┼──────────┘  │
│          │             │             │             │              │
│          ▼             ▼             ▼             ▼              │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │              Python 跟单服务 (单进程 asyncio)               │  │
│  │                                                             │  │
│  │  ┌──────────┐  ┌──────────┐  ┌───────────┐  ┌──────────┐  │  │
│  │  │ 主账户    │  │ 信号处理  │  │ 跟单引擎   │  │ 从账户    │  │  │
│  │  │ 监听器    │→ │ & 风控   │→ │ (比例计算) │→ │ 执行器    │  │  │
│  │  └──────────┘  └──────────┘  └───────────┘  └──────────┘  │  │
│  │                                                             │  │
│  │  ┌──────────────────────────────────────────────────────┐  │  │
│  │  │          FastAPI 管理接口 (localhost:8000)             │  │  │
│  │  │  · 查看跟单状态  · 修改跟单比例  · 紧急停止  · 对账   │  │  │
│  │  └──────────────────────────────────────────────────────┘  │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                   │
│  ┌──────────┐  ┌──────────┐                                      │
│  │PostgreSQL│  │  Redis   │                                      │
│  └──────────┘  └──────────┘                                      │
└──────────────────────────────────────────────────────────────────┘
```

### 5.2 服务器规格

| 组件 | 最小规格 | 推荐规格（10 账户+） |
|---|---|---|
| **CPU** | 4 vCPU | 8 vCPU（每个 MT 终端约消耗 0.5 核） |
| **内存** | 8 GB | 16 GB（每个 MT 终端约 200-500 MB） |
| **磁盘** | 100 GB SSD | 200 GB NVMe（MT 历史数据 + 日志） |
| **OS** | Windows Server 2019 / Windows 10 Pro | 同左（MT 终端必须在 Windows 上运行） |
| **网络** | 100 Mbps | 1 Gbps（低延迟连接经纪商） |

> **注意**：MT4/MT5 终端原生不支持 Linux，如必须用 Linux 服务器，MT5 可通过 Wine 运行（不推荐生产环境），MT4 在 Wine 下 ZeroMQ DLL 兼容性不可靠。

### 5.3 MT 终端数量计算

```
总终端数 = 1（主账户）+ N（从账户数）
         = 1 + 10 = 11 个终端实例（10 从账户场景）

若每个终端 300MB 内存，11 终端 ≈ 3.3 GB 内存仅终端层
推荐：16GB 内存服务器支持最多约 30 个 MT 终端
```

---

## 6. 核心模块详解

### 6.1 账户管理器

管理主账户和从账户的映射关系与跟单配置。

**数据库核心表：**

```sql
-- 主账户
CREATE TABLE lead_accounts (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100),
    mt_version VARCHAR(10),       -- MT4 / MT5
    mt_terminal_path TEXT,        -- 终端安装路径
    mt_login INTEGER,             -- MT 账号
    account_type VARCHAR(20),     -- hedging / netting
    status VARCHAR(20) DEFAULT 'active',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 从账户
CREATE TABLE follower_accounts (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100),
    mt_version VARCHAR(10),
    mt_terminal_path TEXT,
    mt_login INTEGER,
    status VARCHAR(20) DEFAULT 'active',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 跟单配置（核心）
CREATE TABLE copy_configs (
    id SERIAL PRIMARY KEY,
    lead_id INT REFERENCES lead_accounts(id),
    follower_id INT REFERENCES follower_accounts(id),
    copy_ratio DECIMAL(5,4) DEFAULT 1.0,    -- 0.5 = 50%
    min_lot DECIMAL(10,4) DEFAULT 0.01,      -- 最小跟单手数
    max_lot DECIMAL(10,4) DEFAULT 100.0,     -- 最大跟单手数
    symbol_whitelist TEXT[],                  -- 品种白名单
    symbol_blacklist TEXT[],                  -- 品种黑名单
    copy_direction VARCHAR(10) DEFAULT 'both', -- both / long_only / short_only
    copy_sl_tp BOOLEAN DEFAULT true,          -- 是否同步止损止盈
    copy_pending BOOLEAN DEFAULT true,        -- 是否同步挂单
    status VARCHAR(20) DEFAULT 'active',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 订单映射表（核心跟踪表）
CREATE TABLE order_mappings (
    id SERIAL PRIMARY KEY,
    master_ticket BIGINT,                    -- 主订单 ticket
    master_account_id INT,
    master_symbol VARCHAR(20),
    master_volume DECIMAL(10,4),
    master_type VARCHAR(20),                 -- MARKET / LIMIT / STOP / CLOSE
    master_direction VARCHAR(10),            -- BUY / SELL
    master_open_price DECIMAL(20,8),
    master_sl DECIMAL(20,8),
    master_tp DECIMAL(20,8),
    master_status VARCHAR(20),               -- OPEN / CLOSED / MODIFIED
    follower_positions JSONB DEFAULT '[]',   -- 从账户持仓详情数组
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 操作审计日志
CREATE TABLE audit_logs (
    id SERIAL PRIMARY KEY,
    event_type VARCHAR(50),                  -- COPY_OPEN / COPY_CLOSE / COPY_MODIFY / ERROR
    master_account_id INT,
    follower_account_id INT,
    master_ticket BIGINT,
    follower_ticket BIGINT,
    symbol VARCHAR(20),
    volume DECIMAL(10,4),
    details JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

### 6.2 主账户信号检测器

#### MT5 轮询实现

```python
"""
MT5 主账户信号检测器 — 基于快照差量对比
检测逻辑：
  1. 每次轮询获取当前持仓快照
  2. 与 Redis 中缓存的上一轮快照对比
  3. 差量分析：新增 → 开仓信号 / 消失 → 平仓信号 / 变更 → 修改信号
"""

import time
import json
import asyncio
from dataclasses import dataclass
from typing import Optional

import mt5_connector  # 假设的 MT5 连接封装
from redis_client import RedisClient  # Redis 客户端封装


@dataclass
class PositionSnapshot:
    ticket: int
    symbol: str
    type: int       # 0=BUY, 1=SELL
    volume: float
    open_price: float
    sl: float
    tp: float
    comment: str
    open_time: int


class MT5SignalDetector:
    """MT5 主账户信号检测器"""

    def __init__(self, mt5_conn, redis_client: RedisClient,
                 poll_interval_ms: int = 300):
        self.conn = mt5_conn                   # MT5 连接对象
        self.redis = redis_client              # Redis 客户端
        self.poll_interval = poll_interval_ms / 1000.0
        self._running = False

    def _get_snapshot(self) -> dict[int, PositionSnapshot]:
        """获取当前持仓快照，以 ticket 为 key"""
        positions = self.conn.get_positions()  # 调用 mt5.positions_get()
        snapshot = {}
        for p in positions:
            snapshot[p.ticket] = PositionSnapshot(
                ticket=p.ticket, symbol=p.symbol,
                type=p.type, volume=p.volume,
                open_price=p.price_open, sl=p.sl, tp=p.tp,
                comment=p.comment, open_time=p.time
            )
        return snapshot

    def _hash_snapshot(self, snap: dict) -> str:
        """计算快照指纹，用于快速判断是否有变化"""
        hashes = []
        for ticket, pos in sorted(snap.items()):
            hashes.append(
                f"{ticket}:{pos.volume}:{pos.sl}:{pos.tp}"
            )
        return "|".join(hashes)

    async def detect_changes(self):
        """差量检测，返回变更事件列表 [(event_type, old_pos, new_pos)]"""
        events = []

        old_snap = await self.redis.get_json("snapshot:lead:positions")
        new_snap = self._get_snapshot()

        if old_snap is None:
            # 首次启动，全量记录（不触发事件）
            await self.redis.set_json(
                "snapshot:lead:positions", new_snap
            )
            return events

        old_tickets = set(old_snap.keys())
        new_tickets = set(new_snap.keys())

        # 新增持仓 → 开仓信号
        for ticket in new_tickets - old_tickets:
            events.append(("OPEN", None, new_snap[ticket]))

        # 消失的持仓 → 平仓信号
        for ticket in old_tickets - new_tickets:
            events.append(("CLOSE", old_snap[ticket], None))

        # 仍在的持仓 → 检查修改信号（SL/TP 变化）
        for ticket in old_tickets & new_tickets:
            old = old_snap[ticket]
            new = new_snap[ticket]
            if old.sl != new.sl or old.tp != new.tp:
                events.append(("MODIFY", old, new))

        # 更新快照
        await self.redis.set_json(
            "snapshot:lead:positions", new_snap
        )
        return events

    async def run(self):
        """主循环"""
        self._running = True
        while self._running:
            try:
                events = await self.detect_changes()
                for evt in events:
                    await self._publish_event(evt)
            except Exception as e:
                # 记录错误但不中断循环
                pass
            await asyncio.sleep(self.poll_interval)

    async def _publish_event(self, event):
        """将检测到的事件发布到消息队列"""
        evt_type, old_pos, new_pos = event
        payload = {
            "type": evt_type,
            "timestamp": int(time.time() * 1000),
            "old": old_pos.__dict__ if old_pos else None,
            "new": new_pos.__dict__ if new_pos else None,
        }
        await self.redis.xadd("stream:signals", payload)
```

#### MT4 ZeroMQ 事件流（EA 端）

```cpp
// MT4 EA 端伪代码 — 在 OnTrade() 事件中推送交易信号
// 使用 mql-zmq 库的 CZmqSocket

#include <Zmq/Zmq.mqh>

CZmqSocket* g_pubSocket;
string g_lastTradeHash = "";  // 去重用

int OnInit() {
    // 创建 PUB socket，绑定 32770 端口
    g_pubSocket = new CZmqSocket();
    g_pubSocket.bind("tcp://*:32770", ZMQ_PUB);
    return INIT_SUCCEEDED;
}

void OnTrade() {
    // 检测最新成交
    int total = OrdersHistoryTotal();
    if (total == 0) return;

    // 获取最近一笔成交的 ticket
    int ticket = OrderSelect(total - 1, SELECT_BY_POS, MODE_HISTORY)
                 ? OrderTicket() : -1;

    // 去重（同一 tick 可能触发多次 OnTrade）
    string tradeHash = IntegerToString(ticket) + "_"
                     + OrderSymbol() + "_"
                     + DoubleToString(OrderLots(), 2);
    if (tradeHash == g_lastTradeHash) return;
    g_lastTradeHash = tradeHash;

    // 构建 JSON 信号并推送
    string json = StringFormat(
        "{"
        "\"type\":\"TRADE\","
        "\"ticket\":%d,"
        "\"symbol\":\"%s\","
        "\"cmd\":%d,"
        "\"volume\":%.2f,"
        "\"price\":%.5f,"
        "\"sl\":%.5f,"
        "\"tp\":%.5f,"
        "\"time\":%d"
        "}",
        ticket, OrderSymbol(), OrderType(),
        OrderLots(), OrderOpenPrice(),
        OrderStopLoss(), OrderTakeProfit(),
        TimeCurrent()
    );
    g_pubSocket.send(json);
}

void OnDeinit(const int reason) {
    delete g_pubSocket;
}
```

### 6.3 跟单执行引擎

```python
"""
跟单核心执行引擎
处理流程：
  信号 → 查配置 → 风控检查 → 手数计算 → 品种映射 → 逐个从账户下单
"""

import asyncio
import math
from typing import Optional
from decimal import Decimal, ROUND_DOWN


class CopyExecutionEngine:
    """跟单执行引擎"""

    def __init__(self, db_repo, risk_mgr, redis_client):
        self.repo = db_repo        # 数据库访问层
        self.risk = risk_mgr       # 风控管理器
        self.redis = redis_client  # Redis 客户端

    async def handle_open_signal(self, signal: dict):
        """处理开仓信号"""
        master_symbol = signal["symbol"]
        master_volume = signal["volume"]
        master_ticket = signal["ticket"]

        # 1. 查询跟单配置（使用 Redis 缓存）
        configs = await self.repo.get_active_copy_configs(
            lead_id=signal["lead_account_id"]
        )

        follower_positions = []
        for cfg in configs:
            # 2. 品种过滤
            if not self._symbol_allowed(cfg, master_symbol):
                continue

            # 3. 品种映射（处理经纪商后缀差异）
            follower_symbol = self._map_symbol(
                cfg.follower_id, master_symbol
            )

            # 4. 计算跟单手数
            ratio = Decimal(str(cfg.copy_ratio))
            raw_volume = Decimal(str(master_volume)) * ratio

            # 5. 精度截断（按合约规格取整）
            symbol_info = await self.repo.get_symbol_spec(
                cfg.follower_id, follower_symbol
            )
            lot_step = Decimal(str(symbol_info.volume_step))
            volume = (raw_volume / lot_step).to_integral_value(
                rounding=ROUND_DOWN
            ) * lot_step
            volume = float(min(max(volume, cfg.min_lot), cfg.max_lot))

            if volume < symbol_info.volume_min:
                continue  # 低于最小手数，跳过

            # 6. 风控检查
            if not await self.risk.check_position_open(
                follower_id=cfg.follower_id,
                symbol=follower_symbol,
                volume=volume,
                direction=signal["direction"]
            ):
                continue

            # 7. 执行下单（含 500ms 最小间隔控制）
            follower_ticket = await self._place_order(
                account_id=cfg.follower_id,
                mt_version=cfg.mt_version,
                symbol=follower_symbol,
                order_type=signal["order_type"],
                volume=volume,
                price=signal.get("price"),
                sl=self._calc_sl(signal),
                tp=self._calc_tp(signal),
            )

            if follower_ticket:
                follower_positions.append({
                    "account_id": cfg.follower_id,
                    "follower_ticket": follower_ticket,
                    "follower_volume": volume,
                    "copy_ratio": float(ratio),
                })
                await self._audit_log("COPY_OPEN", cfg, follower_ticket)

        # 8. 记录主从映射
        await self.repo.save_order_mapping(
            master_ticket=master_ticket,
            follower_positions=follower_positions,
        )

    async def _place_order(self, account_id, mt_version, symbol,
                           order_type, volume, price=None,
                           sl=None, tp=None) -> Optional[int]:
        """下单执行（按 MT 版本分发）"""
        # 下单间隔控制：确保两次下单间隔 ≥ 500ms
        async with self.redis.rate_limit_lock(
            f"order_interval:{account_id}", min_interval_ms=500
        ):
            if mt_version == "MT5":
                return await self._place_order_mt5(
                    account_id, symbol, order_type,
                    volume, price, sl, tp
                )
            elif mt_version == "MT4":
                return await self._place_order_mt4_zmq(
                    account_id, symbol, order_type,
                    volume, price, sl, tp
                )

    def _map_symbol(self, follower_id: int, master_symbol: str) -> str:
        """品种映射：处理不同经纪商的后缀差异
        例：主账户 XAUUSD → 从账户 XAUUSDm / XAUUSD. / GOLD
        """
        mapping = self.repo.get_symbol_mapping(follower_id)
        return mapping.get(master_symbol, master_symbol)

    def _symbol_allowed(self, config, symbol: str) -> bool:
        """检查品种是否在允许列表中"""
        if config.symbol_whitelist and \
           symbol not in config.symbol_whitelist:
            return False
        if config.symbol_blacklist and \
           symbol in config.symbol_blacklist:
            return False
        return True
```

### 6.4 风控管理器

```python
"""
风控管理器
检查维度：
  1. 从账户单品种最大持仓
  2. 从账户全局最大持仓数
  3. 从账户最大回撤限制
  4. 下单间隔控制（≥ 500ms）
  5. 单日最大亏损限制
"""

class RiskManager:
    def __init__(self, db_repo, redis_client):
        self.repo = db_repo
        self.redis = redis_client

    async def check_position_open(
        self, follower_id: int, symbol: str,
        volume: float, direction: str
    ) -> bool:
        """开仓前综合风控检查"""

        # 1. 单品种最大仓位检查
        current_volume = await self.repo.get_position_volume(
            follower_id, symbol
        )
        max_volume = await self.repo.get_max_volume(
            follower_id, symbol
        )
        if current_volume + volume > max_volume:
            return False

        # 2. 全局最大持仓数
        open_count = await self.repo.get_open_position_count(
            follower_id
        )
        max_count = await self.repo.get_max_positions(follower_id)
        if open_count >= max_count:
            return False

        # 3. 单日最大亏损检查
        daily_pnl = await self.repo.get_daily_pnl(follower_id)
        max_daily_loss = await self.repo.get_max_daily_loss(
            follower_id
        )
        if daily_pnl < -max_daily_loss:
            return False

        # 4. 最大回撤检查
        drawdown = await self._calc_current_drawdown(follower_id)
        max_dd = await self.repo.get_max_drawdown(follower_id)
        if drawdown > max_dd:
            return False

        return True

    async def _calc_current_drawdown(
        self, follower_id: int
    ) -> float:
        """计算当前回撤"""
        equity = await self.repo.get_equity(follower_id)
        peak = await self.repo.get_peak_equity(follower_id)
        if peak == 0:
            return 0.0
        return (peak - equity) / peak
```

### 6.5 状态管理与对账

```
功能：
  · 订单生命周期追踪
  · 定期对账（每 60 秒）
  · 差异检测与自动修复
  · 告警上报

对账逻辑：
  1. 拉取主账户当前全部持仓
  2. 按比例计算每个从账户的预期持仓
  3. 拉取从账户实际持仓
  4. 逐品种、逐方向对比
  5. 差异处理：
     · 从账户缺少持仓 → 补单
     · 从账户多余持仓 → 平仓
     · 从账户手数偏差 > 10% → 调整
     · 从账户 SL/TP 不一致 → 修改
```

---

## 7. MT4/MT5 差异处理

### 7.1 关键差异对照表

| 维度 | MT4 | MT5 |
|---|---|---|
| **Python 交互** | 需要 ZeroMQ DLL 桥接 | 官方 `MetaTrader5` 库 |
| **订单系统** | `OrderSend()` / `OrderClose()` | `order_send()` 统一请求模型 |
| **持仓查询** | `OrdersTotal()` 遍历 | `positions_get()` 直接返回 |
| **挂单查询** | `OrdersTotal()` 过滤 pending | `orders_get()` 直接返回 |
| **部分平仓** | `OrderClose(ticket, lots, ...)` | `order_send(position=ticket, volume=lots, action=DEAL)` |
| **持仓标识** | ticket (int, 终端生命周期唯一) | ticket (int) + position_id (long, 全局唯一) |
| **账户类型** | 仅对冲模式 | 对冲 (Hedging) + 净额 (Netting) |
| **交易品种编码** | 经纪商后缀有差异 | 同左 |
| **历史数据** | `OrdersHistoryTotal()` | `history_deals_get()` + `history_orders_get()` |
| **DLL 依赖** | ZeroMQ 必须加载 DLL | 无需 DLL（官方 Python 库） |
| **Linux 支持** | Wine 运行不稳定 | Wine 可运行但非官方支持 |

### 7.2 统一抽象层设计

```python
"""
MT 平台统一抽象层
屏蔽 MT4/MT5 差异，对外暴露统一接口
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional
from enum import Enum


class OrderType(Enum):
    MARKET_BUY = "market_buy"
    MARKET_SELL = "market_sell"
    BUY_LIMIT = "buy_limit"
    SELL_LIMIT = "sell_limit"
    BUY_STOP = "buy_stop"
    SELL_STOP = "sell_stop"


@dataclass
class Position:
    ticket: int
    symbol: str
    direction: str       # "BUY" / "SELL"
    volume: float
    open_price: float
    sl: float
    tp: float
    open_time: int


@dataclass
class OrderResult:
    success: bool
    ticket: Optional[int] = None
    error_code: Optional[int] = None
    error_msg: Optional[str] = None


class MTPlatformAdapter(ABC):
    """MT 平台适配器抽象基类"""

    @abstractmethod
    def connect(self, terminal_path: str) -> bool: ...

    @abstractmethod
    def get_positions(self, symbol: str = None) -> list[Position]: ...

    @abstractmethod
    def get_pending_orders(self, symbol: str = None) -> list: ...

    @abstractmethod
    def market_order(self, symbol: str, direction: str,
                     volume: float, sl: float = 0,
                     tp: float = 0) -> OrderResult: ...

    @abstractmethod
    def pending_order(self, symbol: str, order_type: OrderType,
                      volume: float, price: float,
                      sl: float = 0, tp: float = 0) -> OrderResult: ...

    @abstractmethod
    def close_position(self, ticket: int,
                       volume: float = None) -> OrderResult: ...

    @abstractmethod
    def modify_position(self, ticket: int,
                        sl: float = None, tp: float = None) -> OrderResult: ...

    @abstractmethod
    def delete_pending_order(self, ticket: int) -> OrderResult: ...

    @abstractmethod
    def get_account_info(self) -> dict: ...

    @abstractmethod
    def get_symbol_info(self, symbol: str) -> dict: ...

    @abstractmethod
    def disconnect(self): ...


class MT5Adapter(MTPlatformAdapter):
    """MT5 适配器 — 使用 MetaTrader5 官方库"""

    def __init__(self):
        import MetaTrader5 as mt5
        self.mt5 = mt5
        self._connected = False

    def connect(self, terminal_path: str) -> bool:
        self._connected = self.mt5.initialize(path=terminal_path)
        return self._connected

    def get_positions(self, symbol: str = None) -> list[Position]:
        if symbol:
            raw = self.mt5.positions_get(symbol=symbol)
        else:
            raw = self.mt5.positions_get()
        if raw is None:
            return []
        return [
            Position(
                ticket=p.ticket, symbol=p.symbol,
                direction="BUY" if p.type == 0 else "SELL",
                volume=p.volume, open_price=p.price_open,
                sl=p.sl, tp=p.tp, open_time=p.time
            )
            for p in raw
        ]

    def market_order(self, symbol: str, direction: str,
                     volume: float, sl: float = 0,
                     tp: float = 0) -> OrderResult:
        request = {
            "action": self.mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": (self.mt5.ORDER_TYPE_BUY
                     if direction == "BUY"
                     else self.mt5.ORDER_TYPE_SELL),
            "sl": sl,
            "tp": tp,
            "deviation": 10,
            "type_filling": self.mt5.ORDER_FILLING_IOC,
        }
        result = self.mt5.order_send(request)
        if result.retcode == self.mt5.TRADE_RETCODE_DONE:
            return OrderResult(success=True, ticket=result.order)
        else:
            return OrderResult(
                success=False, error_code=result.retcode,
                error_msg=result.comment,
            )

    # ... 其他方法类似实现

    def disconnect(self):
        if self._connected:
            self.mt5.shutdown()


class MT4Adapter(MTPlatformAdapter):
    """MT4 适配器 — 通过 ZeroMQ 桥接通信"""

    def __init__(self, zmq_host: str = "localhost",
                 push_port: int = 32768,
                 pull_port: int = 32769):
        import zmq
        self.ctx = zmq.Context()
        # REQ socket → 连接 MT4 EA 的 PUSH 通道（发送命令）
        self.req_socket = self.ctx.socket(zmq.REQ)
        self.req_socket.connect(f"tcp://{zmq_host}:{push_port}")
        # 设置超时避免死等
        self.req_socket.setsockopt(zmq.RCVTIMEO, 5000)
        self._connected = True

    def _send_command(self, command: str,
                      params: dict) -> Optional[dict]:
        """向 MT4 EA 发送命令并等待响应"""
        import json
        msg = json.dumps({
            "command": command,
            "params": params,
        })
        self.req_socket.send_string(msg)
        try:
            response = self.req_socket.recv_string()
            return json.loads(response)
        except Exception:
            return None

    def get_positions(self, symbol: str = None) -> list[Position]:
        resp = self._send_command("GET_POSITIONS", {
            "symbol": symbol or "",
        })
        if not resp or resp.get("status") != "OK":
            return []
        return [
            Position(**p) for p in resp.get("positions", [])
        ]

    def market_order(self, symbol: str, direction: str,
                     volume: float, sl: float = 0,
                     tp: float = 0) -> OrderResult:
        cmd_type = "OP_BUY" if direction == "BUY" else "OP_SELL"
        resp = self._send_command("ORDER_SEND", {
            "symbol": symbol, "cmd": cmd_type,
            "volume": volume, "sl": sl, "tp": tp,
        })
        if resp and resp.get("status") == "OK":
            return OrderResult(
                success=True, ticket=resp.get("ticket")
            )
        else:
            return OrderResult(
                success=False,
                error_msg=resp.get("error", "Unknown error"),
            )

    # ... 其他方法类似
```

---

## 8. 关键挑战与应对

| 挑战 | 风险等级 | 应对策略 |
|---|---|---|
| **MT 终端崩溃** | 🔴 高 | 终端进程监控 + 自动重启 + 重启后补偿同步（全量对账） |
| **下单间隔限制** | 🟡 中 | 全局限流器：所有从账户共享最小 500ms 下单间隔，Redis 分布式锁实现 |
| **品种代码差异** | 🟡 中 | 维护品种映射表（如 `XAUUSD` → `GOLD` / `XAUUSDm` / `XAUUSD.`） |
| **手数精度** | 🟢 低 | 从 `symbol_info` 动态读取 `volume_step` 和 `volume_min`，下单前自动截断 |
| **MT4/MT5 差异** | 🟡 中 | 抽象适配层 `MTPlatformAdapter` 统一接口，底层各自实现 |
| **经纪商限制** | 🟡 中 | 不同经纪商有不同的最大持仓数、最小下单间隔，在配置中按账户单独设置 |
| **部分平仓** | 🟡 中 | 需精确追踪部分平仓后的剩余手数，更新订单映射表中的 volume |
| **循环跟单** | 🔴 高 | 从账户的持仓标注 magic number 或 comment 标识，排除自身触发 |
| **ZeroMQ 连接中断** | 🟡 中 | 心跳检测 + 自动重连 + 重连后全量对账 |
| **MT5 netting 账户** | 🟡 中 | Netting 模式下同一品种只能有一个持仓，跟单逻辑需适配（合并而非新建） |

---

## 9. 实施路线图

| Phase | 内容 | 工期 |
|---|---|---|
| **Phase 1** 基础框架 | Python 项目骨架、数据库模型设计、统一抽象层 `MTPlatformAdapter` | 1 周 |
| **Phase 2** MT5 适配 | `MT5Adapter` 实现、轮询信号检测器、跟单执行引擎（市价单 + 平仓） | 1-2 周 |
| **Phase 3** MT4 适配 | `MT4Adapter`（ZeroMQ 桥接）、MQL4 EA 信号捕获 + 下单 EA | 1-2 周 |
| **Phase 4** 完整跟单 | 挂单同步、SL/TP 修改同步、部分平仓、品种映射 | 1 周 |
| **Phase 5** 风控 & 对账 | 完整风控模块、自动化对账、差异修复 | 1 周 |
| **Phase 6** 运维 & 测试 | 进程守护、监控面板、模拟盘全流程测试、压力测试 | 1 周 |

> **总计预估**：6-8 周

---

## 10. 目录结构建议

```
mt-copy-trading/
├── src/
│   ├── core/
│   │   ├── adapters/
│   │   │   ├── base.py              # MTPlatformAdapter 抽象基类
│   │   │   ├── mt5_adapter.py       # MT5 官方库适配器
│   │   │   └── mt4_adapter.py       # MT4 ZeroMQ 桥接适配器
│   │   ├── detector/
│   │   │   ├── mt5_polling.py       # MT5 轮询信号检测器
│   │   │   └── mt4_zmq_sub.py       # MT4 ZeroMQ SUB 信号接收
│   │   ├── engine/
│   │   │   ├── executor.py          # 跟单执行引擎
│   │   │   └── symbol_mapper.py     # 品种映射
│   │   ├── risk/
│   │   │   └── risk_manager.py      # 风控管理器
│   │   ├── reconciliation/
│   │   │   └── reconciler.py        # 对账模块
│   │   └── rate_limiter.py          # 下单频率控制器（≥500ms）
│   ├── db/
│   │   ├── models.py                # SQLAlchemy 模型
│   │   └── repository.py            # 数据访问层
│   ├── api/
│   │   └── admin_api.py             # 管理接口 (FastAPI)
│   └── main.py                      # 入口
├── mql/
│   ├── mt4/
│   │   ├── SignalCaptureEA.mq4      # 主账户信号捕获 EA
│   │   └── CopyExecutorEA.mq4       # 从账户下单执行 EA
│   └── mt5/
│       └── SignalCaptureEA.mq5      # MT5 可选备用 EA
├── zmq_dll/
│   ├── libzmq.dll                   # ZeroMQ 核心 DLL
│   └── mt4zmq.dll                   # MQL4 ZeroMQ 桥接 DLL
├── config/
│   ├── settings.py                  # 配置管理
│   ├── accounts.yaml.example        # 账户配置模板
│   └── symbol_mapping.yaml          # 品种映射配置
├── tests/
│   ├── unit/
│   └── integration/
├── docker-compose.yml               # Python 服务 + DB + Redis
├── requirements.txt
└── README.md
```

---

## 11. 验证方案

### 11.1 分层测试

| 层级 | 工具 | 覆盖范围 |
|---|---|---|
| **单元测试** | pytest | 比例计算、手数截断、品种映射、风控规则 |
| **集成测试** | pytest + MT 模拟账户 | 连接初始化、持仓查询、下单/平仓 API |
| **端到端测试** | 手动 + 脚本 | 主账户开仓 → 从账户同步验证全链路 |
| **压力测试** | 自定义脚本 | 1 主 + 10 从并发跟单延迟和准确性 |

### 11.2 端到端测试场景

1. **正常开仓跟单**：主账户市价买入 0.1 手 EURUSD → 验证从账户按 50% 比例买入 0.05 手
2. **正常平仓跟单**：主账户平仓 → 验证从账户对应持仓同步平仓
3. **SL/TP 修改**：主账户修改止损 → 验证从账户 SL 同步修改
4. **挂单同步**：主账户挂 Buy Limit → 验证从账户挂同方向限价单
5. **挂单删除**：主账户删除挂单 → 验证从账户挂单同步删除
6. **部分平仓**：主账户平 0.1 手中的 0.05 手 → 验证从账户同步部分平仓
7. **品种映射**：主账户 XAUUSD → 从账户 GOLD 正确映射
8. **MT4/5 混合**：主账户 MT5 → 从账户 MT4（跨版本跟单验证）

### 11.3 监控指标

| 指标 | 说明 | 告警阈值 |
|---|---|---|
| `copy_signal_latency_ms` | 主账户成交 → Python 检测到信号 | > 500ms |
| `copy_execution_latency_ms` | 检测到 → 从账户下单完成 | > 1000ms |
| `copy_success_rate` | 跟单成功率 | < 99% |
| `mt_terminal_online` | MT 终端在线状态 | 连续 2 次心跳丢失 |
| `reconciliation_diff_lots` | 对账手数差异 | > 0.01 手 |
| `order_interval_violations` | 违反最小下单间隔次数 | > 0 |
| `zmq_connection_status` | ZeroMQ 连接状态 (MT4) | 断开 > 10s |

---

## 12. 级联跟单场景

### 12.1 场景定义

**级联跟单**（Cascaded Copy Trading）是指跟单链中存在两级或以上的传递关系：

```
外部策略源（信号提供者 / 跟单平台 / EA）
        │
        ▼
  账户 A（一级从账户：接收外部信号）
        │
        │  ← 本系统负责这一段的跟单
        ▼
  账户 B（二级从账户：跟随账户 A）
        │
        │  ← 可继续级联扩展
        ▼
  账户 C ...（N 级从账户）
```

**典型用例**（你的场景）：
- 账户 A 已经通过外部跟单系统（如 MQL5 信号、跟单平台）接收策略信号
- 现在需要账户 B 也跟随同样的策略，但可以通过账户 A 间接获取信号
- 不需要直接访问外部策略源，只需监控账户 A 的持仓变化

### 12.2 级联跟单的核心原理

本系统的信号检测机制（§6.2）**不关心持仓是如何产生的**——无论是：

| 来源 | 检测方式 |
|---|---|
| 手动交易 | ✅ 持仓快照差量检测 |
| EA 自动交易 | ✅ 同上 |
| 外部跟单平台（MQL5 Signals / 其他） | ✅ 同上 |
| 上一级级联系统 | ✅ 同上 |
| 外部 API 订单 | ✅ 同上 |

系统只做一件事：**对比"上一刻的持仓"和"此刻的持仓"，有变化就触发跟单**。

### 12.3 级联配置步骤

#### Step 1：确认账户 A 的终端环境

```
在账户 A 的 MT 终端上：
  MT5 → 确保 "允许自动交易" 已开启（Python 库需要终端在运行）
  MT4 → 安装信号捕获 EA（§6.2 中的 SignalCaptureEA.mq4），
         该 EA 只读不写，不会干扰账户 A 的现有跟单逻辑
```

#### Step 2：配置账户 A 为主账户

```yaml
# config/accounts.yaml
lead_accounts:
  - name: "账户A-主账户"
    mt_version: "MT4"                    # 或 MT5
    mt_terminal_path: "C:\\MT4_AccountA" # 账户A的MT终端路径
    mt_login: 12345678
    role: "lead"
    comment: "本身是外部信号的从账户，现在作为级联主账户"
```

#### Step 3：配置账户 B 为从账户

```yaml
follower_accounts:
  - name: "账户B-二级从账户"
    mt_version: "MT4"
    mt_terminal_path: "C:\\MT4_AccountB"
    mt_login: 87654321
    role: "follower"

copy_configs:
  - lead_id: "账户A-主账户"
    follower_id: "账户B-二级从账户"
    copy_ratio: 0.5              # 50% 跟单比例
    min_lot: 0.01
    copy_sl_tp: true             # 同步止损止盈
    copy_pending: true           # 同步挂单
    # ⚠️ 关键：comment 过滤，防止循环
    comment_filter: "!RELAY_FROM_B"  # 排除由本系统从B回写的订单
```

#### Step 4：启动系统

```bash
python src/main.py --lead "账户A-主账户" --mode cascade
```

### 12.4 循环触发防护

级联场景中最关键的问题是 **防止循环触发**：

```
错误循环：
  外部源 → 账户A开仓
    → 系统检测到 → 账户B开仓
      → 如果B的订单信息被误读为A的变化
        → 系统再次触发 → 无限循环
```

**三步防护机制：**

| 防护层 | 实现方式 |
|---|---|
| **Layer 1 — Comment 标记** | 系统在账户 B 下单时，自动附加 `comment="[COPY-LEAD-A]"`，信号检测器识别并忽略自身产生的订单 |
| **Layer 2 — Magic Number 隔离** | 如果 MT 终端支持，使用不同的 `magic_number` 区分本系统订单和外部订单 |
| **Layer 3 — 信号来源校验** | 检测器中维护"已知自身订单 ticket"的 Redis Set，一旦订单由本系统发出，其 ticket 立即加入忽略列表 |

```python
# 信号检测器中的循环防护逻辑（伪代码）
class CascadeSafeDetector:
    def __init__(self):
        self.self_issued_tickets = set()  # 本系统发出的订单 ticket
        self.comment_marker = "[COPY-LEAD-"  # 本系统的 comment 前缀

    def mark_as_self_issued(self, ticket: int):
        """标记为系统自身发出的订单，不触发跟单"""
        self.self_issued_tickets.add(ticket)

    def should_ignore(self, position) -> bool:
        """判断是否应忽略该持仓变化"""
        # Layer 1: comment 匹配
        if position.comment.startswith(self.comment_marker):
            return True
        # Layer 2: ticket 已知
        if position.ticket in self.self_issued_tickets:
            return True
        return False
```

### 12.5 级联延迟计算

```
T0: 外部源发出信号
    │
T1: 账户A经纪商执行 (网络延迟: 10-200ms)
    │
T2: 本系统检测到账户A变化
    │   ├── MT5 轮询: +0~300ms
    │   └── MT4 ZeroMQ: +<1ms
    │
T3: 本系统在账户B执行 (处理+下单: 50-200ms)
    ▼
总级联延迟 (T0→T3):
  · 使用 MT4 ZeroMQ: 60-400ms
  · 使用 MT5 轮询:   160-700ms
```

> **建议**：级联场景下，账户 A 推荐使用 **MT4 + ZeroMQ 推送方案**，以最小化账户 A→B 的增量延迟。

### 12.6 多级级联扩展

系统原生支持任意深度的级联链：

```
账户A (主) → 账户B (从, 50%)
           → 账户C (从, 30%)
           → 账户D (从, 100%)

配置要点：
  - 每个从账户在 copy_configs 中独立配置比例和过滤规则
  - 所有从账户的订单统一标记 comment，防止交叉触发
  - 对账模块按 lead_id 分组，分别对账
```

若需要 **三级以上级联**（如 A→B→C），只需重复配置：将 B 也加入 `lead_accounts`，C 作为 B 的从账户。系统会为每对主从关系独立维护订单映射。

---

## 13. 方案延迟对比

### 13.1 各方案延迟拆解

```
方案                   信号检测         网络/处理         订单执行          总延迟
─────────────────────────────────────────────────────────────────────────────
MT5 轮询 (200ms)      0~200ms          ~10ms            ~50-200ms       60~410ms
MT5 轮询 (500ms)      0~500ms          ~10ms            ~50-200ms       60~710ms
MT4 ZeroMQ 推送       <1ms (OnTrade)   ~1ms(ZMQ)+10ms   ~50-200ms       51~212ms
MT4 文件管道          文件IO延迟        轮询间隔+IO       ~50-200ms       80~500ms+
MT5 EA + ZeroMQ       <1ms (OnTrade)   ~1ms(ZMQ)+10ms   ~50-200ms       51~212ms
```

### 13.2 延迟对策略类型的影响

| 策略类型 | 可接受延迟 | 推荐方案 |
|---|---|---|
| **日内手动交易** | < 2 秒 | 任意方案均可 |
| **日内 EA 策略** | < 1 秒 | MT5 轮询 (200ms) 或 MT4 ZMQ |
| **短线/剥头皮** | < 500ms | MT4 ZeroMQ 推送（必须） |
| **中长线/波段** | < 5 秒 | 任意方案均可 |
| **级联跟单** | 尽量低 | MT4 ZeroMQ 推送（建议） |

### 13.3 选择决策树

```
你的策略频率？
  ├── 剥头皮 / 短线 (< M1)
  │   └── 必须用 MT4 ZeroMQ 推送（延迟 < 100ms）
  │
  ├── 日内 / M5-M15
  │   ├── 有 DLL 权限？ → MT4 ZeroMQ 推送（最优）
  │   └── 无 DLL 权限？ → MT5 轮询 200ms（可接受）
  │
  ├── H1 / H4
  │   └── MT5 轮询 500ms（足够，部署最简单）
  │
  └── 级联跟单（账户 A→B）
      ├── 推荐 MT4 ZeroMQ 推送
      └── 最低部署成本：MT5 轮询 200ms
```

### 13.4 延迟优化技巧

| 技巧 | 预期效果 |
|---|---|
| Python 与 MT 终端运行在同一台机器 | 消除网络延迟（节省 5-50ms） |
| 使用 `asyncio` 异步处理多账户 | 避免串行阻塞（节省 50-200ms/账户） |
| Redis 本地部署（127.0.0.1） | 消除缓存网络延迟 |
| ZeroMQ 使用 IPC 而非 TCP（同机） | 节省 ~1ms |
| 预计算品种映射和精度规则并缓存 | 每次下单节省 10-50ms |
| MT5 `order_send` 使用 `ACTION_DEAL` + `FILLING_IOC` | 减少订单验证时间 |

> **总结建议**：对于你的级联跟单场景（账户 A → 账户 B），推荐 **MT4 ZeroMQ 推送方案**，端到端延迟控制在 **50-200ms**，避免轮询方案带来的额外 300-500ms 延迟叠加。

---

## 14. 大规模跟单管理方案

> **场景**：从初期 1 对 1 或 1 对 2，逐步扩展到 1 对 10、1 对 50 甚至更多。当从账户超过 10 个后，手动管理变得不可控，需要一个系统化的管理方案。

### 14.1 扩展挑战全景图

```
规模增长的痛点：

  1-3 从账户          10-20 从账户           50+ 从账户
  ────────            ──────────            ──────────
  手动配置 ✅          配置管理混乱 ❌        配置管理不可控 ❌
  人工监控 ✅          人工监控吃力 ❌        必须自动化监控 ✅
  单机轻松跑 ✅        单机压力增大 ⚠️       需要分片部署 ❌
  出问题及时人工修 ✅   难以及时发现 ❌        需要自动对账修复 ✅
  对账手动做 ✅        对账耗时长 ❌          必须有自动化对账 ✅
```

### 14.2 五维管理方案

#### 维度一：账户生命周期管理

```
账户状态机：

  REGISTERED → PENDING_VERIFY → ACTIVE → PAUSED → STOPPED
   (已注册)      (待验证)       (跟单中)   (暂停)   (已停止)
                                   │                   │
                                   └─── ERROR ─────────┘
                                        (异常)

操作流程：
  1. REGISTERED:  录入 API 信息、配置跟单比例
  2. PENDING_VERIFY: 连接测试 → 余额验证 → 品种检查
  3. ACTIVE:      正常跟单运行
  4. PAUSED:      手动/自动暂停（触发风控时自动暂停）
  5. STOPPED:     退出跟单，平掉所有跟单持仓
  6. ERROR:       连接断开 / 余额不足 / 连续失败 → 自动标记
```

**管理 API 接口：**

```yaml
# 账户管理 REST API
POST   /api/v1/followers              # 注册新从账户
GET    /api/v1/followers              # 列出所有从账户（分页+筛选）
GET    /api/v1/followers/{id}/status  # 查看单个从账户状态
PUT    /api/v1/followers/{id}/config  # 修改跟单配置
POST   /api/v1/followers/{id}/pause   # 暂停跟单
POST   /api/v1/followers/{id}/resume  # 恢复跟单
POST   /api/v1/followers/{id}/stop    # 停止跟单（平仓后退出）
DELETE /api/v1/followers/{id}         # 删除从账户
POST   /api/v1/followers/batch        # 批量操作（批量修改比例等）
```

#### 维度二：MT 终端资源池化

当从账户越来越多时，每个账户启动一个独立 MT 终端实例不现实（10 个终端 × 300MB = 3GB，50 个 = 15GB）。

**方案 A：MT5 多账户共享终端（推荐）**

```
一台 MT5 终端可以同时连接多个交易账户（通过 Python 库切换）：

  VPS (单台)
  ├── MT5 终端实例 #1（主账户 A）
  │     · 信号检测（轮询）
  │
  ├── MT5 终端实例 #2（经纪商 X 的从账户组）
  │     ├── 从账户 B1 (login: 10001)
  │     ├── 从账户 B2 (login: 10002)
  │     ├── ... (最多同经纪商下所有账户)
  │     └── 从账户 B10 (login: 10010)
  │
  └── MT5 终端实例 #3（经纪商 Y 的从账户组）
        ├── 从账户 C1
        └── ...

  优势：终端数 = 经纪商数量，而非账户数量
  内存：10 个从账户用同一经纪商 ≈ 1 个 MT5 终端 = 300MB
```

**方案 B：MT4 多实例管理（轻量方案）**

```
MT4 不支持单终端多账户，需要多实例。但可以用"便携模式"降低开销：

  每个 MT4 实例 = 独立的安装目录，共享相同的程序文件
  内存优化：使用 /portable 模式，每个实例占 150-250MB

  C:\MT4\
  ├── Instance_LeadA\     ← 主账户 A
  ├── Instance_F01\       ← 从账户 01
  ├── Instance_F02\       ← 从账户 02
  └── ...

  管理技巧：
  · 用脚本批量创建实例目录
  · 进程监控自动拉起崩溃的实例
  · 按经纪商分组，同经纪商共享行情连接
```

**方案 C：终端与 Python 服务分离（大规模推荐）**

```
  机器 1 (VPS-LEAD)           机器 2 (VPS-FOLLOWER)
  ┌──────────────────┐       ┌────────────────────────┐
  │ 主账户 MT4 终端   │       │ 从账户组 #1 (10 个 MT4) │
  │ Python 信号检测器  │ TCP   │ 从账户组 #2 (10 个 MT4) │
  │                  │──────▶│ 从账户组 #3 (10 个 MT4) │
  │                  │       │ Python 下单执行器       │
  └──────────────────┘       └────────────────────────┘

  优势：
  · 主账户端和从账户端独立扩展
  · 从账户端可用多台 VPS 分片承载
  · 信号通过 TCP 传输（延迟可控在 1-5ms 内网）
```

#### 维度三：批量运维面板

```python
"""
管理员仪表板核心功能
"""

# === 仪表板端点的关键数据聚合 ===

GET /dashboard/overview
  Response:
  {
    "lead_account": {
      "name": "主账户A",
      "balance": 50000.00,
      "equity": 52300.00,
      "open_positions": 3,
      "today_pnl": 1230.50,
      "terminal_online": true,
      "last_heartbeat": "2026-06-18T14:35:00Z"
    },
    "followers_summary": {
      "total": 25,                    # 注册的从账户总数
      "active": 22,                   # 正常跟单中
      "paused": 1,                    # 暂停中
      "error": 2,                     # 异常状态
      "total_equity": 85000.00,       # 所有从账户净值合计
      "total_pnl_today": 2450.30,     # 今日总盈亏
      "avg_copy_latency_ms": 185,     # 平均跟单延迟
      "copy_success_rate": 99.7       # 跟单成功率
    },
    "alerts": [
      {"level": "ERROR", "msg": "从账户F18 余额不足，已暂停跟单"},
      {"level": "WARN", "msg": "从账户F07 跟单延迟 1200ms，超过阈值"}
    ]
  }

GET /dashboard/followers?status=error
  # 快速筛选出异常账户

GET /dashboard/followers/F15/detail
  # 单个从账户详情：
  # 实时持仓 vs 预期持仓、历史跟单记录、盈亏曲线、延迟趋势
```

#### 维度四：分组管理与模板配置

```yaml
# config/follower_groups.yaml
# 将从账户分组，相同组共享配置模板

groups:
  - name: "保守组"
    description: "低风险跟单，30% 比例，仅跟主流货币对"
    template:
      copy_ratio: 0.3
      max_lot: 1.0
      symbol_whitelist: ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD"]
      stop_loss_multiplier: 1.0     # 原样复制止损
      max_drawdown_pct: 15           # 最大回撤 15%
      max_daily_loss_usd: 500        # 单日最大亏损 $500
    followers: ["F01", "F02", "F03", "F04", "F05"]

  - name: "标准组"
    description: "50% 跟单，中等风险"
    template:
      copy_ratio: 0.5
      max_lot: 5.0
      symbol_whitelist: []
      stop_loss_multiplier: 1.0
      max_drawdown_pct: 25
      max_daily_loss_usd: 2000
    followers: ["F06", "F07", "...", "F20"]

  - name: "激进组"
    description: "100% 跟单，高风险高收益"
    template:
      copy_ratio: 1.0
      max_lot: 10.0
      max_drawdown_pct: 40
      max_daily_loss_usd: 5000
    followers: ["F21", "F22", "F23"]
```

**批量操作示例：**

```python
# 批量修改跟单比例的 API
POST /api/v1/followers/batch
{
  "action": "update_config",
  "group": "保守组",                # 按组批量操作
  # 或者直接指定账户列表
  "follower_ids": ["F01", "F03", "F05"],
  "updates": {
    "copy_ratio": 0.4,             # 统一调整为 40%
    "max_daily_loss_usd": 800
  }
}
```

#### 维度五：自动化对账与告警

```
对账策略随规模演进：

  少量账户 (< 10):
    每 5 分钟全量对账 ✅
    手动查看差异 ✅

  中等规模 (10-50):
    每 5 分钟增量对账（只检查有变化的账户）
    差异自动修复 + 飞书/钉钉/Telegram 通知
    Dashboard 红黄绿状态一眼可见

  大规模 (50+):
    每 5 分钟采样对账（轮流检查）
    每 30 分钟全量对账
    差异分级处理：
      · 手数偏差 < 2% → 自动修复，静默
      · 手数偏差 2-10% → 自动修复 + 通知
      · 手数偏差 > 10% → 暂停该账户 + 人工介入
```

### 14.3 规模化架构演进路线图

```
阶段 1: 试点期 (1-3 从账户)
  · 单台 VPS，全组件合一
  · Docker Compose 部署
  · 管理靠配置文件 + 日志
  · 成本：$20-30/月

阶段 2: 增长期 (5-20 从账户)
  · 单台 VPS（升级到 8 vCPU / 16GB）
  · 引入管理 Dashboard (FastAPI + 简单前端)
  · 分组模板管理
  · 自动化告警（钉钉/Telegram）
  · 成本：$60-100/月

阶段 3: 规模化 (20-50 从账户)
  · 2-3 台 VPS 分片
  · 主账户端独立 VPS + 从账户端 VPS 集群
  · PostgreSQL 主从分离（读写分离）
  · Redis Cluster
  · 完善的管理后台 + 操作审计
  · 成本：$200-400/月

阶段 4: 平台化 (50+ 从账户)
  · K8s 集群
  · 多数据中心部署
  · 自动扩缩容
  · 完善的 API 对外开放
  · 成本：按需扩展
```

### 14.4 快速参考：各规模下的资源配置

| 从账户数 | VPS 规格 | MT 终端数 | 部署方式 | 跟单延迟 |
|---|---|---|---|---|
| 1-5 | 4 vCPU / 8GB | 1-6 个 MT4 实例 | 单机 Docker Compose | 50-200ms |
| 5-15 | 8 vCPU / 16GB | 1-16 个 MT4 实例 | 单机 + 管理面板 | 50-250ms |
| 15-30 | 2 台 VPS (8C/16G) | 分组部署 | 主从分离 | 50-300ms |
| 30-50 | 3-4 台 VPS | 分片到 4 台机器 | 负载均衡 | 50-400ms |
| 50+ | K8s 集群 | 按经纪商聚合 | 容器化编排 | 自定义 SLA |

### 14.5 关键运维 Checklist

```markdown
## 每日检查
- [ ] Dashboard 概览页：无红色告警
- [ ] 所有从账户 heartbeat 正常
- [ ] 跟单成功率 ≥ 99%
- [ ] 延迟 P99 ≤ 1000ms

## 每周检查
- [ ] 对账差异汇总为零或已处理
- [ ] 磁盘使用率 ≤ 70%
- [ ] 审计日志无异常操作
- [ ] 各从账户净值曲线与主账户趋势一致

## 每月检查
- [ ] API Key 轮换提醒（有效期检查）
- [ ] 成本核算：VPS 费用 vs 跟单收益
- [ ] 性能压测：当前配置能否应对下月增长
- [ ] 备份恢复演练
```

---

## 15. MetaTrader Signals 评估

> 问题：MT4 自带的「信号」（Signals）功能是否适合我们的跟单需求？延迟会不会更低？

### 15.1 MetaTrader Signals 是什么

MetaTrader Signals 是 MT4/MT5 **内置**的信号订阅功能，运作模式如下：

```
信号提供者（注册在 MQL5.com）
        │
        │ 交易数据自动上传
        ▼
  MQL5 云服务器（MetaQuotes 运营）
        │
        │ 订阅者的 MT4 终端定期轮询
        ▼
  订阅者 MT4 终端（自动复制交易）
```

它是一个**云端中继**架构，信号数据绕经 MQL5 的服务器，而非直接在终端之间传输。

### 15.2 延迟对比：Signals vs 自建方案

| 方案 | 信号路径 | 典型延迟 | 最坏延迟 |
|---|---|---|---|
| **MT4 Signals（内置）** | 提供者 → MQL5 云 → 订阅者轮询 | **2-30 秒** | 60 秒+ |
| **自建 ZeroMQ 推送** | EA OnTrade() → localhost → Python | **50-200 毫秒** | 500 毫秒 |
| **自建 MT5 轮询** | 主账户持仓 → Python 检测 → 从账户 | **60-410 毫秒** | 700 毫秒 |
| **本地文件 EA 跟单器** | EA 写文件 → 另一 EA 读文件 | **1-50 毫秒** | 200 毫秒 |

> **结论：MetaTrader Signals 的延迟是自建方案的 10-600 倍**。它不是为低延迟设计的，而是为"开箱即用"的便利性设计的。

### 15.3 Signs 的延迟来源拆解

```
延迟环节                    耗时
─────────────────────────────────────
提供者终端上传交易到 MQL5 云      1-5 秒（非实时，批量上报）
MQL5 云处理 + 分发                1-3 秒
订阅者终端轮询间隔                5-30 秒（默认每 5-30 秒同步一次）
订阅者终端执行订单                 <1 秒
─────────────────────────────────────
总计                              7-38 秒
```

核心问题在于：**订阅者终端是主动轮询 MQL5 服务器，而非服务器主动推送**。这个轮询间隔由 MT4 内部控制，无法配置到亚秒级。

### 15.4 Signs 的功能限制（对你的场景致命）

| 限制 | 影响 |
|---|---|
| **只支持一个信号订阅** | 账户 A 如果正在接收外部信号，它**不能同时作为信号的提供者**。即 A 无法同时是"订阅者"和"提供者" |
| **不复制挂单** | Pending Orders（LIMIT/STOP）不会同步，如果你的策略有挂单，账户 B 会缺失 |
| **初始同步要求苛刻** | 订阅时要求订阅者**无持仓**，如果账户 B 已有仓位，必须先全部平仓 |
| **订阅者必须盈利时才能同步** | 如果信号提供者有浮亏，初始同步可能失败 |
| **比例固定，无法自定义** | 跟单比例由"订阅者余额/提供者余额"自动决定，不能手动设为 50% |
| **无品种过滤** | 不能只跟某些品种，提供者所有交易都会复制 |
| **无风控自定义** | 不能设置单日最大亏损、最大回撤等保护 |
| **交易数据公开** | 账户 A 需在 MQL5.com 注册为信号提供者，交易记录公开可见 |

### 15.5 适合 vs 不适合的场景

#### ✅ 适合用 MetaTrader Signals 的情况

```
场景：两个独立账户想跟随同一个公开信号源

  信号提供者（MQL5.com 上注册的策略师）
      │
      ├── 账户 A（订阅）
      └── 账户 B（订阅）

条件：
  · 信号提供者已在 MQL5.com 公开
  · 不需要自定义跟单比例
  · 可以接受 5-30 秒的延迟
  · 只需要市价单复制（不需要挂单）
  · 不需要风控定制

这种情况直接用 Signals，零代码，零维护。
```

#### ❌ 不适合用 MetaTrader Signals 的情况

```
场景一：外部信号源不在 MQL5 上

  自定义 EA / 跟单平台 → 账户 A → 账户 B
                               │
                               └── 无法用 Signals：
                                   账户 A 不是 MQL5 信号提供者

场景二：需要级联跟单

  外部源 → 账户 A（订阅者）→ 账户 B
                              │
                              └── 无法用 Signals：
                                  一个账户不能同时是
                                  订阅者 + 提供者

场景三：需要低延迟

  短线/剥头皮策略要求 < 500ms
  Signals 的 5-30 秒完全不可接受

场景四：需要自定义控制

  自定义跟单比例 / 品种过滤 / 风控规则
  Signals 一概不支持
```

### 15.6 一个值得考虑的特殊情况

**如果外部信号源已经在 MQL5 Signals 上注册了：**

```
之前设想的链路：
  外部源 → 账户A → 账户B
  （延迟：外部→A + A→B）

可以直接改为：
  外部源(MQL5 Signals) → 账户A
  外部源(MQL5 Signals) → 账户B
  （延迟：外部→A 和 外部→B 同时发生）

优势：
  · 消除了 A→B 的级联延迟
  · 不再需要自建跟单系统
  · 零代码

代价：
  · 延迟仍是 5-30 秒（Signals 本身的限制）
  · 不能自定义跟单比例/风控
  · 短期策略不可用
```

> **判断方法**：检查你的外部信号来源 —— 如果是 MQL5.com 上的公开信号，则 A 和 B 直接都去订阅那个信号源；如果不是（比如是自建 EA、第三方跟单平台），则 MetaTrader Signals 完全不适用。

### 15.7 所有方案终极对比

```
                        延迟       自定义      挂单    部署     级联
                        等级       跟单比例    支持    成本    支持
──────────────────────────────────────────────────────────────────
MT4 内置 Signals        2-30s ❌   自动 ❌     不支持   零    ❌
第三方 EA 跟单器        1-50ms ✅  灵活 ✅     支持    $50-200 ❌
（本地文件/共享内存）
自建 ZeroMQ 方案        50-200ms ✅  完全 ✅    支持    VPS   ✅
（本方案）
自建 MT5 轮询方案       60-410ms ✅  完全 ✅    支持    VPS   ✅
（本方案）
──────────────────────────────────────────────────────────────────
```

### 15.8 最终建议

针对你的场景（外部源 → 账户 A → 账户 B）：

| 判断条件 | 推荐方案 |
|---|---|
| 外部信号源在 MQL5.com | A 和 B **直接订阅同一信号源**，绕过 A→B 级联（但延迟 2-30s） |
| 外部信号源不在 MQL5.com | **MetaTrader Signals 不适用**，用自建 ZeroMQ 方案（50-200ms） |
| 中长线策略（H4+），延迟不敏感 | 假如外部源恰好在 MQL5 上，Signals 可用；否则仍用自建 |
| 短线/日内策略 | **必须自建**，Signals 的延迟完全不可接受 |

> **一句话**：MetaTrader Signals 的延迟比自建方案**高 10-600 倍**，而且无法支持级联跟单（A 不能同时做订阅者+提供者）。对于你的场景，**Signals 不适用，自建 ZeroMQ 方案仍然是正确选择**。

---

## 附录

### A. 环境依赖

```bash
# Python 依赖安装
pip install MetaTrader5==5.0.45   # MT5 官方库
pip install pyzmq==25.1.2         # ZeroMQ Python 绑定（MT4 桥接用）
pip install asyncpg==0.29.0       # PostgreSQL 异步驱动
pip install redis==5.0.8          # Redis 客户端
pip install sqlalchemy[asyncio]==2.0.35  # ORM
pip install fastapi==0.115.0      # 管理 API
pip install pydantic-settings==2.5.0  # 配置管理

# 仅 MT4 方案额外需要
# 将 zmq_dll/ 下的 libzmq.dll 和 mt4zmq.dll 复制到 MT4 安装目录
```

### B. 参考资源

- [MetaTrader5 Python 官方文档](https://www.mql5.com/zh/docs/python_metatrader5)
- [Darwinex DWX ZeroMQ Connector](https://github.com/darwinex/dwx-zeromq-connector) — MT4 ZeroMQ 桥接参考
- [dingmaotu/mql-zmq](https://github.com/dingmaotu/mql-zmq) — MQL4/MQL5 ZeroMQ 绑定库
- [vuonglx/MT5CopyBot](https://github.com/vuonglx/MT5CopyBot) — MT5 Python 跟单参考实现
- [MQL-ZMQ 通信桥梁技术解析](https://blog.gitcode.com/7c0f4fba101ed48067a9ce4b562b1c73.html)
