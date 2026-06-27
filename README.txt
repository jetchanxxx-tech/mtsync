MT Copy Trading System v1.0.0
========================================

Real-time trade copying system for MetaTrader 5 platforms.
Supports 1:N account synchronization with sub-100ms latency.

PREREQUISITES:
  - Windows 10/11 64-bit
  - MetaTrader 5 terminal(s) installed
  - At least 1 lead account (signal source) + 1 follower account (target)
  - Algo Trading enabled in MT5: Tools > Options > Expert Advisors

QUICK START:
  1. Launch MT_Copy_Trading.exe
  2. Page 1: Configure MT5 paths
     - Lead: Path to signal source MT5 terminal
     - Follower: Path to copy target MT5 terminal(s)
     - Use [+] to add more follower accounts
  3. Click "Start System"
  4. Page 2: Monitor real-time copy operations
     - Filter logs by category
     - View sync latency in milliseconds
     - Save logs for auditing

ARCHITECTURE:
  Signal Source MT5 -> Monitor Process -> ZMQ -> Executor Process -> Target MT5
  End-to-end latency: ~100ms (50ms poll + 0.17ms ZMQ + 50ms execution)
  Supports 1:N: add more executor processes for additional followers

SUPPORT:
  Website: https://example.com
  Email: support@example.com

VERSION HISTORY:
  1.0.0 - Initial release
    - Real-time signal detection (snapshot diff algorithm)
    - Proportional copy execution with precision truncation
    - Risk management (position limits, drawdown, daily loss)
    - Position reconciliation
    - GUI with live latency monitoring
