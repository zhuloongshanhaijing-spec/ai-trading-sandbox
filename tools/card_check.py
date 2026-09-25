# -*- coding: utf-8 -*-
"""假设卡自动记分器：盘后对预注册卡验收（防事后归因漂移）。
用法: python3 tools/card_check.py [YYYY-MM-DD]
输出: 各项改动 预期vs实测 + PASS/FAIL/PENDING + 明日建议
"""
import json
import os
import re
import sys
import time
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO_DATA = os.path.join(ROOT, "demo", "tool-data")
RUNTIME = os.environ.get("PAPERLAB_RUNTIME_DIR", os.path.join(ROOT, ".paperlab-runtime"))


def day_arg():
    a = sys.argv[1] if len(sys.argv) > 1 else time.strftime("%Y-%m-%d")
    return a if re.match(r"\d{4}-\d{2}-\d{2}", a) else time.strftime("%Y-%m-%d")


def main():
    day = day_arg()
    entries, exits = [], []
    decisions_path = os.environ.get("PAPERLAB_DECISIONS_PATH", os.path.join(DEMO_DATA, "decisions.jsonl"))
    for line in open(decisions_path, encoding="utf-8"):
        try:
            d = json.loads(line)
        except Exception:
            continue
        ts = str(d.get("ts", ""))
        if not ts.startswith(day):
            continue
        det = str(d.get("detail", ""))
        if d.get("type") == "entry":
            m = re.search(r"动量([\d.]+)%", det)
            b = re.search(r"盘口(\d+):(\d+)", det)
            ratio = int(b.group(1)) / max(int(b.group(2)), 1) if b else None
            entries.append({"ts": ts, "code": d["code"], "mom": float(m.group(1)) if m else None,
                            "ratio": ratio, "t1": "T1反转" in det})
        elif d.get("type") == "exit":
            g = re.search(r"([-+]?\d+\.?\d*)%", det)
            if g:
                r = ("止盈" if "止盈" in det else "止损" if "止损" in det
                     else "超时" if "超时" in det else "其他")
                exits.append({"ts": ts, "code": d["code"], "pct": float(g.group(1)), "r": r})
    st = {}
    state_path = os.environ.get("PAPERLAB_STATE_PATH")
    if state_path:
        try:
            st = json.load(open(state_path, encoding="utf-8"))
        except Exception:
            pass
    spike_shadows = [s for s in st.get("shadows_done", [])
                     if "强动量拦" in str(s.get("reason", ""))
                     and s.get("ts", 0) > time.mktime(time.strptime(day, "%Y-%m-%d"))]
    imb = [e for e in entries if e["ratio"] and e["ratio"] >= 4]
    stops = [e for e in exits if e["r"] == "止损"]
    h15 = [e for e in entries if e["ts"][11:13] == "15"]
    gross = sum(e["pct"] for e in exits) / max(len(exits), 1)
    lines = ["# 假设卡记分 %s（自动验收）" % day, ""]
    rows = [
        ("尖顶闸门: 失衡入场占比<15%", "%.0f%% (%d/%d)" % (
            100 * len(imb) / max(len(entries), 1), len(imb), len(entries)),
         "PASS" if len(imb) / max(len(entries), 1) < 0.15 and entries else "PENDING" if not entries else "FAIL"),
        ("止损限价: 止损均值改善(≤-1.6%)", "%.2f%% (%d笔)" % (
            sum(s["pct"] for s in stops) / max(len(stops), 1) if stops else 0, len(stops)),
         "PASS" if stops and sum(s["pct"] for s in stops) / len(stops) >= -1.6 else
         "PENDING" if not stops else "FAIL"),
        ("调速器降档: 全天入场≤8笔", "%d笔" % len(entries),
         "PASS" if len(entries) <= 8 else "FAIL"),
        ("毒性时段: 15时入场=0", "%d笔" % len(h15), "PASS" if not h15 else "FAIL"),
        ("强动量影子: 审计在途", "%d条终局" % len(spike_shadows),
         "观察" ),
        ("当日费前均值(成功定义: ≥-0.35%合格/≥0良好)", "%+.2f%%/笔" % gross,
         "良好" if gross >= 0 else "合格" if gross >= -0.35 else "未达标"),
    ]
    lines.append("| 预期 | 实测 | 判定 |")
    lines.append("|---|---|---|")
    for a, b, c in rows:
        lines.append("| %s | %s | %s |" % (a, b, c))
    lines.append("")
    lines.append("样本进度: 当日离场 %d 笔 | 影子终局累计 %d 条" % (
        len(exits), len(st.get("shadows_done", []))))
    out = "\n".join(lines)
    path = os.path.join(RUNTIME, "reports", "CARD-%s.md" % day)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(out + "\n")
    print(out)
    print("\n→ %s" % path)


if __name__ == "__main__":
    main()
