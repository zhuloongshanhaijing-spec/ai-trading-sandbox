#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI Trading Sandbox 合成演示数据生成器（确定性：固定种子，不含任何真实市场/平台数据）。

用法：python3 demo/generate_demo_data.py
输出：
  demo/data/            → 面板演示服务所需（trades/nodes/snapshot/params/kline）
  demo/tool-data/stock_kline.jsonl → tools/ml_score.py 可直接研究的合成日K
  demo/tool-data/decisions.jsonl   → tools/card_check.py 可验收的合成决策流
"""
import json
import os
import random
import time
from datetime import datetime, timedelta

random.seed(42)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO = os.path.join(ROOT, "demo", "data")
TOOL_DATA = os.path.join(ROOT, "demo", "tool-data")

SYMS = [
    ("DM0001", "示例科技"), ("DM0002", "示例能源"), ("DM0003", "示例医药"),
    ("DM0004", "示例消费"), ("DM0005", "示例制造"), ("DM0006", "示例金融"),
]
FUTS = [("FM9001", "示例螺纹"), ("FM9002", "示例甲醇")]

NOW = datetime.now().replace(microsecond=0)
TODAY = NOW.strftime("%Y-%m-%d")


def ts(dtx):
    return dtx.strftime("%Y-%m-%d %H:%M:%S")


def gen_daily():
    """每标的120根日K（随机游走），返回 {code: bars}，bar={date,o,h,l,c,v}。"""
    out = {}
    for code, _ in SYMS:
        px = random.uniform(12, 60)
        bars = []
        day = NOW - timedelta(days=180)
        while len(bars) < 120:
            day += timedelta(days=1)
            if day.weekday() >= 5 or day > NOW:  # 周末跳过，且不越过今天
                if day > NOW:
                    break
                continue
            chg = random.gauss(0.0006, 0.018)
            o = round(px, 2)
            c = round(max(1.0, px * (1 + chg)), 2)
            h = round(max(o, c) * (1 + abs(random.gauss(0, 0.006))), 2)
            l = round(min(o, c) * (1 - abs(random.gauss(0, 0.006))), 2)
            bars.append({"date": day.strftime("%Y-%m-%d"), "o": o, "h": h, "l": l, "c": c,
                         "v": random.randint(80, 900) * 10000})
            px = c
        out[code] = bars
    return out


def gen_intraday(daily):
    """最近3个交易日的5分K（由日K收盘价衍生随机游走）。"""
    out = {}
    for code, bars in daily.items():
        pts = []
        base = bars[-1]["c"]
        for k in range(-3, 0):
            day = NOW + timedelta(days=k)
            if day.weekday() >= 5 or day.date() == NOW.date() is False and day.date() > NOW.date():
                continue
            d0 = day.replace(hour=9, minute=30, second=0)
            px = base * (1 + random.gauss(0, 0.004))
            for i in range(48):
                t = d0 + timedelta(minutes=5 * i)
                if t > NOW:
                    break
                px = max(1.0, px * (1 + random.gauss(0, 0.0012)))
                o = round(px, 2)
                c = round(max(1.0, px * (1 + random.gauss(0, 0.0012))), 2)
                pts.append({"t": int(t.timestamp()), "o": o,
                            "h": round(max(o, c) * 1.0008, 2), "l": round(min(o, c) * 0.9992, 2),
                            "c": c, "v": random.randint(5, 90) * 100})
        out[code] = pts
    return out


def gen_trades(daily):
    stocks, decisions = [], []
    kinds = ["stop", "tp", "timeout"]
    day_cursor = NOW - timedelta(days=58)
    for i in range(46):
        code, name = random.choice(SYMS)
        bars = daily[code]
        b = random.choice(bars[-90:])
        entry = round(b["c"] * random.uniform(0.995, 1.005), 2)
        held = random.randint(35, 380)
        kind = random.choice(kinds)
        if kind == "stop":
            pct = -random.uniform(1.2, 1.6)
        elif kind == "tp":
            pct = random.uniform(2.4, 3.2)
        else:
            pct = random.uniform(-0.8, 1.2)
        exit_px = round(entry * (1 + pct / 100), 2)
        d0 = datetime.strptime(b["date"], "%Y-%m-%d").replace(
            hour=random.randint(9, 15), minute=random.randint(0, 59))
        if d0 > NOW - timedelta(minutes=30):
            d0 = NOW - timedelta(days=1, minutes=random.randint(60, 300))
        d1 = min(d0 + timedelta(minutes=held), NOW - timedelta(minutes=5))
        why_in = "动量%.2f%% 盘口%d:%d@%.2f 额%d万 涨%.2f%%" % (
            random.uniform(0.3, 1.1), random.randint(80, 400), random.randint(20, 120),
            entry, random.randint(800, 9000), random.uniform(1.0, 5.5))
        why_out = {"stop": "止损离场 -%.2f%%", "tp": "止盈离场 +%.2f%%",
                   "timeout": "超时离场 %+.2f%%"}[kind] % pct
        stocks.append({"market": "stock", "code": code, "name": name,
                       "entry_ts": ts(d0), "entry_px": entry, "why_in": why_in,
                       "t1": random.random() < 0.15, "side": "买",
                       "exit_ts": ts(d1), "exit_px": exit_px, "lots": 1,
                       "why_out": why_out, "pct": round(pct, 2), "status": "closed"})
        decisions.append({"ts": ts(d0), "type": "entry", "code": code, "action": "buy",
                          "detail": why_in})
        decisions.append({"ts": ts(d1), "type": "exit", "code": code, "action": "sell",
                          "detail": why_out})
    # 2笔持有中
    for code, name in random.sample(SYMS, 2):
        entry = round(random.uniform(15, 55), 2)
        d0 = NOW - timedelta(hours=random.randint(2, 30))
        stocks.append({"market": "stock", "code": code, "name": name,
                       "entry_ts": ts(d0), "entry_px": entry,
                       "why_in": "动量%.2f%% 盘口%d:%d@%.2f 额%d万 涨%.2f%%" % (
                           random.uniform(0.3, 1.0), random.randint(80, 300),
                           random.randint(20, 100), entry, random.randint(800, 6000),
                           random.uniform(1.0, 4.0)),
                       "t1": False, "side": "买", "exit_ts": None, "exit_px": None,
                       "lots": 1, "why_out": None, "pct": None, "status": "open"})
    futures = []
    for i in range(8):
        code, name = random.choice(FUTS)
        entry = round(random.uniform(2400, 3600), 1)
        pnl = round(random.gauss(120, 900), 0)
        d0 = NOW - timedelta(days=random.randint(1, 40), minutes=random.randint(0, 500))
        d1 = d0 + timedelta(minutes=random.randint(30, 400))
        futures.append({"market": "futures", "code": code, "name": name,
                        "entry_ts": ts(d0), "entry_px": entry,
                        "why_in": "信号A 尖顶反做 摆幅%.2f%%" % random.uniform(0.5, 1.2),
                        "t1": False, "side": random.choice(["多", "空"]),
                        "exit_ts": ts(min(d1, NOW)), "exit_px": round(entry * random.uniform(0.99, 1.01), 1),
                        "lots": 1,
                        "why_out": "止盈离场" if pnl > 0 else "止损离场",
                        "pnl": pnl, "status": "closed"})
    shadows = []
    for i in range(12):
        code, name = random.choice(FUTS)
        d0 = NOW - timedelta(days=random.randint(1, 30))
        shadows.append({"name": name, "exit_ts": ts(d0 + timedelta(hours=2)),
                        "pct_open": round(random.uniform(0.4, 2.0), 2),
                        "pct_end": round(random.uniform(-1.5, 2.5), 2),
                        "move": round(random.uniform(-2.0, 3.0), 2),
                        "held_min": random.randint(40, 300)})
    return stocks, futures, shadows, decisions


def gen_nodes(stocks, curve):
    """6个参数版本节点；为每个节点挂载期间交易与统计（面板'参数版本'页数据源）。"""
    node_defs = [
        ("2026-01-05 09:30:00", "stock", "动量臂首上线：小步试探",
         "先用最小仓位试水追涨打法：谁短期涨得快、买盘挂单厚，就买一点；亏到止损线或到时没涨就撤。", [], "demo seed"),
        ("2026-01-12 10:00:00", "stock", "假设卡：入场过滤收紧",
         "样本显示假突破多发，预注册假设卡锁定：提高可信涨幅门槛，超时持仓更快撤出。",
         [{"path": "signal.wall_ratio", "old": None, "new": 4.0},
          {"path": "signal.stop_limit_offset", "old": None, "new": 0.1}], "demo seed"),
        ("2026-01-20 15:00:00", "stock", "调速器（CUSUM）上线",
         "波动自适应节流：市场噪声大自动降到底档（少仓少笔），趋势确认后恢复全速。",
         [{"path": "signal.cusum_k", "old": None, "new": 0.15},
          {"path": "signal.cusum_h", "old": None, "new": 7.5}], "demo seed"),
        ("2026-02-02 08:30:00", "stock", "移动止盈启用",
         "浮盈达到2%后改为跟踪止盈，回撤1.2%落袋——让利润奔跑而不是固定3%就跑。",
         [{"path": "signal.trail_arm_pct", "old": None, "new": 2.0},
          {"path": "signal.trail_lock_pct", "old": None, "new": 1.2}], "demo seed"),
        ("2026-02-18 09:00:00", "stock", "日亏熔断纪律",
         "单日总资产回撤达到2%即当日停止入场，次日复盘后恢复——铁律级风控。",
         [{"path": "signal.daily_loss_halt_pct", "old": None, "new": 2.0}], "demo seed"),
        ("2026-03-10 10:00:00", "stock", "T1反转臂接入",
         "连跌后的次日反弹研究规则纳入模拟信号（演示统计仅用于展示），与动量规则互补。",
         [{"path": "signal.t1_enabled", "old": 0, "new": 1}], "demo seed"),
    ]
    nodes = []
    for i, (t, scope, title, plain, changes, source) in enumerate(node_defs):
        t_next = node_defs[i + 1][0] if i + 1 < len(node_defs) else "2099-01-01 00:00:00"
        trs = [x for x in stocks if x["status"] == "closed"
               and t <= x["entry_ts"] < t_next]
        pcts = [x["pct"] for x in trs if x.get("pct") is not None]
        c0 = [c for c in curve if c[0] <= t]
        eq_delta = round(c0[-1][1] - c0[0][1]) if len(c0) > 1 else 0
        nodes.append({"ts": t, "scope": scope, "title": title, "plain": plain,
                      "changes": changes, "source": source,
                      "stats": {"n": len(trs),
                                "winrate": round(100 * sum(1 for p in pcts if p > 0) / len(pcts), 1) if pcts else None,
                                "mean_pct": round(sum(pcts) / len(pcts), 2) if pcts else None,
                                "worst": min(pcts) if pcts else None,
                                "open_now": sum(1 for x in stocks if x["status"] == "open"),
                                "eq_delta": eq_delta},
                      "eq_pts": [c for c in curve if c[0] <= t][::4],
                      "trades": trs[:40]})
    return nodes


def gen_curve():
    """600个净值采样点（3分钟粒度→跨约60个交易日，这里用小时粒度近似）。"""
    pts, total = [], 1_000_000.0
    t = NOW - timedelta(days=60)
    step = (NOW - t).total_seconds() / 600
    for i in range(600):
        total += random.gauss(60, 900)
        pts.append([ts(t + timedelta(seconds=step * i)), round(total, 0)])
    return pts


def main():
    os.makedirs(DEMO, exist_ok=True)
    os.makedirs(TOOL_DATA, exist_ok=True)
    daily = gen_daily()
    intraday = gen_intraday(daily)
    stocks, futures, shadows, decisions = gen_trades(daily)
    curve = gen_curve()
    nodes = gen_nodes(stocks, curve)

    with open(os.path.join(DEMO, "trades.json"), "w", encoding="utf-8") as f:
        json.dump({"stocks": stocks, "futures": futures, "shadows": shadows}, f, ensure_ascii=False)
    with open(os.path.join(DEMO, "nodes.json"), "w", encoding="utf-8") as f:
        json.dump({"nodes": nodes}, f, ensure_ascii=False)
    for code, bars in daily.items():
        with open(os.path.join(DEMO, "kline_%s_1d.jsonl" % code), "w", encoding="utf-8") as f:
            for b in bars:
                f.write(json.dumps({"date": b["date"], "o": b["o"], "h": b["h"], "l": b["l"],
                                    "c": b["c"], "v": b["v"]}, ensure_ascii=False) + "\n")
        with open(os.path.join(DEMO, "kline_%s_5m.jsonl" % code), "w", encoding="utf-8") as f:
            for b in intraday[code]:
                f.write(json.dumps(b, ensure_ascii=False) + "\n")

    open_tr = [x for x in stocks if x["status"] == "open"]
    last_px = {c: (daily[c][-1]["c"] if daily.get(c) else 20) for c, _ in SYMS}
    positions = [{"code": t["code"], "name": t["name"], "lots": t["lots"],
                  "cost": t["entry_px"], "price": last_px.get(t["code"], t["entry_px"]),
                  "day_pct": round(random.uniform(-2.5, 3.5), 2),
                  "upnl": round((last_px.get(t["code"], t["entry_px"]) - t["entry_px"]) * 100, 0)}
                 for t in open_tr]
    mv = sum(p["price"] * 100 * p["lots"] for p in positions)
    total = float(curve[-1][1])
    journal = ["[%s] 心跳 | 最强动量: %s %+.2f%% | AI仓 %d/5 | 账户 %d" %
               (ts(NOW - timedelta(minutes=3 * i)), random.choice(SYMS)[1],
                random.uniform(-1, 3), len(open_tr), total) for i in range(30)]
    journal.append("[%s] T1重算 龙头组2只:%s(%s)|%s(%s)|连跌阈值-8%%" %
                   (ts(NOW.replace(hour=9, minute=20)), SYMS[0][1], SYMS[0][0], SYMS[1][1], SYMS[1][0]))
    journal.append("[%s] 入场闸门[调速器CUSUM(S-12.4/S+0.0)→仓位≤1] 3只候选转影子" % ts(NOW - timedelta(minutes=10)))
    snapshot = {"account": {"time": ts(NOW), "total": round(total), "cash": round(total - mv),
                            "market_value": round(mv),
                            "position_pct": round(100 * mv / total, 1)},
                "positions": positions, "points": curve,
                "journal": journal[-40:],
                "ai_positions": {t["code"]: True for t in open_tr},
                "universe_count": 12, "dry_run": True, "halted": False,
                "shadows": {"done": 12, "open": 2, "mean_pct": 0.4},
                "futures": {"playbook": "idle", "risk_lock": False, "journal_last": journal[0]}}
    with open(os.path.join(DEMO, "snapshot.json"), "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False)

    params = {"stock_cfg": {"signal": {
        "t1_enabled": 1, "k1_enabled": 0, "shadow_enabled": 1,
        "momentum_pct": 0.25, "entry_mom_max": 1.0, "stop_pct": 1.5, "target_pct": 3.0,
        "trail_arm_pct": 2.0, "trail_lock_pct": 1.2, "timeout_cycles": 160,
        "max_positions": 5, "max_trades_per_hour": 4, "min_entry_gap_sec": 300,
        "daily_loss_halt_pct": 2.0, "cusum_k": 0.15, "cusum_h": 7.5,
        "wall_ratio": 4.0, "stop_limit_offset": 0.1, "entry_hour_max": 15,
        "min_amount": 800, "imbalance_ratio": 1.2, "depth_ratio_min": 0.3,
        "notional_cap_yuan": 50000, "chase_day_pct_max": 7.0, "day_pct_min": 0.5,
        "fake_mom_max": 1.5, "fake_ask_vol": 500,
        "throttle_tier1": [1, 1], "throttle_tier2": [5, 4], "blocked_hours": [12]}},
        "futures_cfg": {"observer_enabled": True, "engine_enabled": True, "enable_live": False,
                        "risk_new_action_max": 60,
                        "signal_a": {"move_pct": 0.6, "retrace_pct": 0.15, "tp_pct": 1.2, "sl_pct": 0.8},
                        "signal_b": {"mom_pct": 0.4}}}
    with open(os.path.join(DEMO, "params.json"), "w", encoding="utf-8") as f:
        json.dump(params, f, ensure_ascii=False)

    # 工具输入：只写入随库的合成演示目录，绝不使用用户数据目录。
    with open(os.path.join(TOOL_DATA, "stock_kline.jsonl"), "w", encoding="utf-8") as f:
        for code, bars in daily.items():
            for b in bars:
                f.write(json.dumps({"code": code, "date": b["date"], "o": b["o"], "h": b["h"],
                                    "l": b["l"], "c": b["c"], "v": b["v"],
                                    "prev_close": b["o"]}, ensure_ascii=False) + "\n")
    with open(os.path.join(TOOL_DATA, "decisions.jsonl"), "w", encoding="utf-8") as f:
        for d in decisions:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    with open(os.path.join(TOOL_DATA, "config_change_log.jsonl"), "w", encoding="utf-8") as f:
        for nd in nodes:
            f.write(json.dumps({k: nd[k] for k in
                                ("ts", "scope", "title", "plain", "changes", "source", "stats")},
                               ensure_ascii=False) + "\n")

    print("演示数据生成完毕：股票%d笔(含%d持有) 期货%d笔 影子%d条 节点%d个 → %s" %
          (len(stocks), len(open_tr), len(futures), len(shadows), len(nodes), DEMO))


if __name__ == "__main__":
    main()
