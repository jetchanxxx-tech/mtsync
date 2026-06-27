# -*- coding: utf-8 -*-
"""
Automated copy trading test - simulate external signal trigger + measure latency.

Usage: python scripts/auto_test.py
"""

import sys
import os
import time
import subprocess
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import MetaTrader5 as mt5

LEAD_PATH = r"C:\Program Files\MetaTrader 5\terminal64.exe"
FOLLOWER_PATH = r"D:\MetaTrader 5\terminal64.exe"
MONITOR_SCRIPT = os.path.join(os.path.dirname(__file__), "monitor.py")
EXECUTOR_SCRIPT = os.path.join(os.path.dirname(__file__), "executor.py")
TEST_SYMBOL = "EURUSD"
TEST_VOLUME = 0.01
MAGIC = 888000


def find_symbol(symbols, base):
    for s in symbols:
        if s.name == base or s.name.startswith(base):
            return s.name
    return None


def now_str():
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def main():
    print("=" * 55)
    print("  MT Copy Trading - Automated Integration Test")
    print("  Signal Source: 711591 (simulate external signal)")
    print("  Copy Target:   711621 (receive copy)")
    print("=" * 55)

    results = []
    processes = []

    try:
        # Step 1: Start processes
        print("\n[Step 1/6] Starting monitor + executor...")
        t0 = time.perf_counter()

        p_monitor = subprocess.Popen(
            [sys.executable, MONITOR_SCRIPT],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            env={**os.environ, "LEAD_OVERRIDE_PATH": LEAD_PATH},
        )
        processes.append(("monitor", p_monitor))
        time.sleep(2)

        p_executor = subprocess.Popen(
            [sys.executable, EXECUTOR_SCRIPT],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            env={**os.environ, "FOLLOWER_OVERRIDE_PATH": FOLLOWER_PATH},
        )
        processes.append(("executor", p_executor))
        time.sleep(3)

        if p_monitor.poll() is not None:
            print("  FAIL monitor startup (code=%s)" % p_monitor.returncode)
            return
        if p_executor.poll() is not None:
            print("  FAIL executor startup (code=%s)" % p_executor.returncode)
            return

        print("  OK monitor + executor running (startup %.0fms)" % ((time.perf_counter() - t0) * 1000))

        # Step 2: Verify initial state
        print("\n[Step 2/6] Verify initial state...")

        mt5.initialize(path=LEAD_PATH)
        lead_acc = mt5.account_info()
        lead_symbol = find_symbol(mt5.symbols_get(), TEST_SYMBOL)
        lead_pos_before = len(mt5.positions_get()) if mt5.positions_get() else 0
        print("  Signal: %d | %s | %s | positions=%d" % (lead_acc.login, lead_symbol or "N/A", lead_acc.server, lead_pos_before))
        mt5.shutdown()

        mt5.initialize(path=FOLLOWER_PATH)
        follow_acc = mt5.account_info()
        follow_symbol = find_symbol(mt5.symbols_get(), TEST_SYMBOL)
        follow_pos_before = len(mt5.positions_get()) if mt5.positions_get() else 0
        print("  Target: %d | %s | %s | positions=%d" % (follow_acc.login, follow_symbol or "N/A", follow_acc.server, follow_pos_before))
        mt5.shutdown()

        if not lead_symbol:
            print("  FAIL: signal source has no %s" % TEST_SYMBOL)
            return
        if not follow_symbol:
            print("  FAIL: target has no %s" % TEST_SYMBOL)
            return

        # Step 3: Simulate external signal - open on lead
        print("\n[Step 3/6] Simulate external OPEN signal on %s %.2f lot..." % (lead_symbol, TEST_VOLUME))

        mt5.initialize(path=LEAD_PATH)
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": lead_symbol,
            "volume": TEST_VOLUME,
            "type": mt5.ORDER_TYPE_BUY,
            "price": 0.0,
            "deviation": 10,
            "magic": 0,
            "comment": "ext_signal",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        t_signal_sent = time.perf_counter()
        lead_result = mt5.order_send(request)
        t_signal_done = time.perf_counter()

        if lead_result is None or lead_result.retcode != 10009:
            err = lead_result.comment if lead_result else str(mt5.last_error())
            print("  FAIL open lead: %s" % err)
            mt5.shutdown()
            return

        lead_ticket = lead_result.order
        lead_price = lead_result.price
        lead_time = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        print("  OK lead open: ticket=%d price=%.5f (%.0fms)" % (lead_ticket, lead_price, (t_signal_done - t_signal_sent) * 1000))
        mt5.shutdown()

        # Step 4: Wait for copy
        print("\n[Step 4/6] Waiting for copy system to sync...")
        t_wait_start = time.perf_counter()
        follow_ticket = None
        follow_price = None
        t_copy_detected = None

        for i in range(30):
            time.sleep(0.5)
            # drain executor output
            try:
                while True:
                    line = p_executor.stdout.readline()
                    if not line:
                        break
                    if any(kw in line for kw in ["OPEN", "CLOSE", "MODIFY", "master", "follower", "SUCCESS", "SKIPPED"]):
                        print("  [executor] %s" % line.rstrip())
            except Exception:
                pass

            mt5.initialize(path=FOLLOWER_PATH)
            positions = mt5.positions_get()
            mt5.shutdown()

            if positions:
                for p in positions:
                    if p.magic == MAGIC:
                        follow_ticket = p.ticket
                        follow_price = p.price_open
                        t_copy_detected = time.perf_counter()
                        break
                if follow_ticket:
                    break

            if i % 4 == 0:
                sys.stdout.write("\r  waiting... %.1fs" % (i * 0.5))
                sys.stdout.flush()

        if follow_ticket is None:
            print("\n  FAIL: copy not received (timeout 15s)")
            results.append({"test": "Copy OPEN", "status": "FAILED", "error": "timeout"})
        else:
            sync_ms = (t_copy_detected - t_signal_done) * 1000
            print("\n  OK copy received!")
            print("  +-- follower ticket: %d  price: %.5f" % (follow_ticket, follow_price))
            print("  +-- sync latency: %.0f ms" % sync_ms)
            results.append({
                "test": "Copy OPEN",
                "status": "SUCCESS",
                "lead_ticket": lead_ticket,
                "follower_ticket": follow_ticket,
                "lead_time": lead_time,
                "sync_latency_ms": round(sync_ms, 1),
                "lead_price": lead_price,
                "follower_price": follow_price,
            })

        # Step 5: Simulate external close
        if lead_ticket:
            print("\n[Step 5/6] Simulate external CLOSE signal (lead ticket=%d)..." % lead_ticket)

            mt5.initialize(path=LEAD_PATH)
            close_req = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": lead_symbol,
                "volume": TEST_VOLUME,
                "type": mt5.ORDER_TYPE_SELL,
                "position": lead_ticket,
                "price": 0.0,
                "deviation": 10,
                "magic": 0,
                "comment": "ext_close",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            t_close_sent = time.perf_counter()
            close_result = mt5.order_send(close_req)
            t_close_done = time.perf_counter()

            lead_profit = 0
            if close_result and close_result.retcode == 10009:
                lead_profit = close_result.profit
                print("  OK lead close: profit=%.2f (%.0fms)" % (lead_profit, (t_close_done - t_close_sent) * 1000))
            else:
                print("  FAIL lead close: %s" % (close_result.comment if close_result else mt5.last_error()))
            mt5.shutdown()

            # Wait for copy close
            print("  Waiting for copy close sync...")
            follow_closed = False
            t_close_sync = None

            for i in range(30):
                time.sleep(0.5)
                try:
                    while True:
                        line = p_executor.stdout.readline()
                        if not line:
                            break
                        if any(kw in line for kw in ["CLOSE", "master", "follower", "SUCCESS"]):
                            print("  [executor] %s" % line.rstrip())
                except Exception:
                    pass

                mt5.initialize(path=FOLLOWER_PATH)
                positions = mt5.positions_get()
                mt5.shutdown()

                found = False
                if positions:
                    for p in positions:
                        if p.ticket == follow_ticket:
                            found = True
                            break
                if not found:
                    follow_closed = True
                    t_close_sync = time.perf_counter()
                    break

            if follow_closed:
                close_ms = (t_close_sync - t_close_done) * 1000
                print("  OK copy closed! latency: %.0f ms" % close_ms)
                results.append({
                    "test": "Copy CLOSE",
                    "status": "SUCCESS",
                    "lead_ticket": lead_ticket,
                    "follower_ticket": follow_ticket,
                    "sync_latency_ms": round(close_ms, 1),
                    "lead_profit": round(lead_profit, 2),
                })
            else:
                print("  WARN copy close not detected within 15s")
                results.append({"test": "Copy CLOSE", "status": "WARN", "error": "timeout"})

        # Step 6: Report
        print("\n" + "=" * 55)
        print("  TEST REPORT")
        print("=" * 55)
        print()

        for i, r in enumerate(results):
            icon = "OK" if r["status"] == "SUCCESS" else ("WARN" if r["status"] == "WARN" else "FAIL")
            print("  [%s] %s" % (icon, r["test"]))
            if r["status"] == "SUCCESS":
                print("       master ticket: %d  ->  follower ticket: %d" % (r["lead_ticket"], r["follower_ticket"]))
                print("       sync latency: %.0f ms" % r["sync_latency_ms"])
                if "lead_price" in r and "follower_price" in r:
                    diff = abs(r["follower_price"] - r["lead_price"])
                    print("       price diff: %.5f (lead=%.5f, follow=%.5f)" % (diff, r["lead_price"], r["follower_price"]))
                if "lead_profit" in r:
                    print("       lead profit: %.2f" % r["lead_profit"])
            else:
                print("       error: %s" % r.get("error", "unknown"))
            print()

        overall = all(r["status"] == "SUCCESS" for r in results)
        if overall and len(results) >= 2:
            total_ms = sum(r.get("sync_latency_ms", 0) for r in results)
            print("  >>> ALL TESTS PASSED | total sync latency: %.0f ms <<<" % total_ms)
        elif results:
            print("  >>> SOME TESTS FAILED - check logs <<<")

        print("\n" + "=" * 55)

    finally:
        print("\n  Cleaning up processes...")
        for name, p in processes:
            try:
                p.terminate()
                p.wait(timeout=3)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
        print("  Done\n")


if __name__ == "__main__":
    main()
