#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""参数变更日志——参数实验纪律工具。

本工具要求每次认知动作（改信号参数）先记录可审计的参数版本节点：
**每次参数/机制改动，先落账一条节点，再改配置文件。** 这样面板才能做
"参数版本→绩效"对照，复盘时每笔交易都能归因到它当时的参数版本。

用法：
  记录节点： python3 tools/config_change.py log --scope stock --title "标题" \
              --plain "白话解释这次改了什么、为什么" \
              [--change "signal.stop_pct=1.0->1.5" ...] [--source 依据] [--actor ai]
  查看全部： python3 scripts/config_change.py list
  历史回填： python3 scripts/config_change.py seed    （幂等；来自文档考证的里程碑）

scope: stock | futures | goal（goal=组织/目标变更，无参数 diff）
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME = os.environ.get("PAPERLAB_RUNTIME_DIR", os.path.join(ROOT, ".paperlab-runtime"))
LOG = os.path.join(RUNTIME, "config_change_log.jsonl")

# 文档考证的历史里程碑（幂等回填；backfill=true 表示非当时实时记录）
SEEDS = [
    {"ts": "2026-01-05 09:30:00", "scope": "stock", "title": "动量臂首上线：小步试探",
     "plain": "先用最小仓位试水\"追涨\"打法：谁短期涨得快、买盘挂单厚，就买一点；亏到止损线或到时没涨就撤。",
     "changes": [], "source": "demo seed", "backfill": True},
    {"ts": "2026-01-12 10:00:00", "scope": "stock", "title": "假设卡：入场过滤收紧",
     "plain": "样本显示假突破多发，预注册假设卡锁定：把\"什么样的涨才可信\"的门槛提高，超时持仓更快撤出。",
     "changes": [
         {"path": "signal.wall_ratio", "old": None, "new": 4.0, "desc": "假买墙判定收紧"},
         {"path": "signal.stop_limit_offset", "old": None, "new": 0.1, "desc": "止损限价偏移%"}],
     "source": "demo seed", "backfill": True},
    {"ts": "2026-01-20 15:00:00", "scope": "futures", "title": "期货引擎切回影子模式",
     "plain": "信号阈值按历史数据校准后，保持影子模式积累模拟样本；后续是否调整由研究者复核。",
     "changes": [
         {"path": "mode", "old": "active", "new": "shadow", "desc": "模拟引擎模式"},
         {"path": "enable_execution", "old": True, "new": False, "desc": "执行开关"}],
     "source": "demo seed", "backfill": True},
]


def _load():
    rows = []
    if os.path.exists(LOG):
        with open(LOG, encoding="utf-8") as f:
            for L in f:
                L = L.strip()
                if L:
                    try:
                        rows.append(json.loads(L))
                    except Exception:
                        pass
    return rows


def _append(entry):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def cmd_log(a):
    changes = []
    for c in a.change or []:
        path, _, rest = c.partition("=")
        old, _, new = rest.partition("->")
        changes.append({"path": path, "old": old or None, "new": new or None})
    entry = {"ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "scope": a.scope,
             "title": a.title, "plain": a.plain, "changes": changes,
             "source": a.source or "人工记录", "actor": a.actor, "backfill": False}
    _append(entry)
    print("已记录节点：%s %s [%s]" % (entry["ts"], entry["title"], entry["scope"]))


def cmd_seed(a):
    have = {(r.get("ts"), r.get("title")) for r in _load()}
    n = 0
    for s in SEEDS:
        if (s["ts"], s["title"]) in have:
            continue
        _append(s | {"backfill": True})
        n += 1
    print("回填完成：新增 %d 个里程碑节点（共 %d 种子）" % (n, len(SEEDS)))


def cmd_list(a):
    rows = _load()
    rows.sort(key=lambda r: str(r.get("ts", "")))
    for r in rows:
        print("%s  [%s] %s" % (r.get("ts"), r.get("scope"), r.get("title")))
        for c in r.get("changes") or []:
            print("      %s: %s -> %s" % (c.get("path"), c.get("old"), c.get("new")))
    print("共 %d 个节点" % len(rows))


def main():
    p = argparse.ArgumentParser(description="参数变更日志（参数版本节点）")
    sub = p.add_subparsers(dest="cmd", required=True)
    lg = sub.add_parser("log", help="记录一个参数变更节点")
    lg.add_argument("--scope", required=True, choices=["stock", "futures", "goal"])
    lg.add_argument("--title", required=True)
    lg.add_argument("--plain", required=True, help="白话解释：改了什么、为什么")
    lg.add_argument("--change", action="append", help="path=old->new 可多次")
    lg.add_argument("--source", help="依据（报告/数据）")
    lg.add_argument("--actor", default="ai", help="stock_ai / futures_ai / user")
    sub.add_parser("seed", help="幂等回填文档考证的历史里程碑")
    sub.add_parser("list", help="查看全部节点")
    a = p.parse_args()
    {"log": cmd_log, "seed": cmd_seed, "list": cmd_list}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
