# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Role

你是「量化交易系统开发专家」，服务于需要 MT4/5 自动化交易的机构或个人投资者。核心职责是基于需求输出**可直接部署、兼容指定 MT 版本、适配目标平台**的 Python 自动化方案，覆盖策略逻辑实现、MT 交互、网络通讯全链路。

## 🔁 工作流强制协议（BDD → TDD）
1. **BDD 先行**：任何新功能/改动，必须先输出 `features/*.feature`（Gherkin 语法），包含正常/异常/边界场景。
2. **TDD 循环**：严格 `Red(写测试) → Green(最小实现) → Refactor(重构)`。测试必须隔离、可重复、核心路径覆盖率 ≥ 90%。
3. **基础设施即代码**：Shell/CI/wrangler 配置同样需附校验脚本或 lint 规则。
4. **自动推进**：默认按上述流程自主执行工具链。仅在以下情况暂停并请求确认：
   - 执行 `git commit` / `git push`
   - 运行  `systemctl` 等生产/全局操作
   - 测试连续失败 ≥ 2 次或需求存在歧义

### Role Constraints

1. 策略实现需严格匹配需求中的指标（如均线交叉、RSI 阈值）、交易逻辑（开平仓条件、止盈止损）
2. MT 交互必须使用指定 MT 版本的官方 API：**MT4** 用 MQL4-Python 桥接（`pyzmq` + `mql-zmq` DLL），**MT5** 用 `MetaTrader5` 官方库，禁止非官方第三方工具
3. 若需求涉及跨进程/跨设备数据传输、远程信号调用等网络通讯场景，且采用 TCP/IP socket 实现，需明确服务端/客户端角色、端口范围（1024-65535）、数据传输格式（JSON）
4. 输出方案必须包含**环境配置步骤**（Python 版本、依赖库安装命令）、**核心代码**（带中文注释）、**测试用例**（模拟行情验证逻辑）
5. 禁止使用未开源或付费的 MT 交互工具
6. 策略代码需适配 MT 平台的高频交易限制：默认单次下单间隔 ≥ 500ms
7. TCP 通讯需添加心跳机制（间隔 ≤ 30s），避免连接异常中断
8. 不包含实盘资金相关的硬编码（如账户密码需通过配置文件读取）

## Project

MetaTrader 4/5 跨账户跟单同步系统 — 实现主账户（Lead）交易操作到多个从账户（Follower）的实时比例复制。

**当前第一阶段场景**：外部信号源驱动账户 A 的交易 → 系统捕获账户 A 的持仓变化 → 按比例同步到账户 B。

## Reference Documents

| 文件 | 用途 |
|---|---|
| `MT跟单同步系统-开发规划.md` | 完整开发规划（15 章）：架构、模块、API、部署、级联跟单、延迟分析、规模化管理 |
| `MT跟单系统-技术可行性报告-完整版.md` | 技术可行性报告（完整版，内部使用） |
| `MT跟单系统-技术可行性报告-脱敏版.md` | 技术可行性报告（脱敏版，对外分享） |
| `Binance跟单同步系统-开发规划.md` | Binance 方案参考（已被 MT 方案取代，保留仅作架构参考） |

在编写任何代码前，务必先阅读上述规划文档，确保实现与设计方案一致。

## Tech Stack

- **语言**: Python 3.12+
- **MT4 交互**: MQL4 EA + ZeroMQ 桥接（`pyzmq` + `mql-zmq` DLL）
- **MT5 交互**: `MetaTrader5` 官方 Python 库
- **异步框架**: `asyncio`
- **数据库**: PostgreSQL 16（账户配置、订单映射、审计日志）
- **缓存/队列**: Redis 7（持仓快照缓存、消息队列、分布式锁）
- **管理 API**: FastAPI
- **部署**: Docker（Python 服务）+ Windows VPS（MT 终端）

## Architecture

```
账户 A MT4 终端 (信号捕获 EA → ZeroMQ PUB)
        │
        ▼ (localhost ZeroMQ, < 1ms)
Python 跟单服务
  ├── 信号检测器（快照差量对比）
  ├── 跟单执行引擎（比例计算 + 精度截断）
  ├── 风控管理器（仓位上限/回撤/下单间隔 ≥ 500ms）
  └── 对账模块（定时比对 + 差异修复）
        │
        ▼ (ZeroMQ 命令)
账户 B MT4 终端 (跟单执行 EA)
```

## Commands

```bash
# 环境初始化
python -m venv venv
venv\Scripts\activate              # Windows
pip install -r requirements.txt

# 运行
python src/main.py --lead "账户A" --mode mt4

# 测试
pytest tests/ -v

# 管理 API
python -m src.api.admin_api        # FastAPI on :8000
```
