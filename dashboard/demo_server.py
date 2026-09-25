#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI Trading Sandbox 演示面板服务：用 demo/ 合成数据驱动 dashboard/panel.html 全部API。

仅标准库；AI体检为可选功能（需本地Ollama，默认 qwen3:8b，完全本地推理）。
启动：python3 dashboard/demo_server.py  →  http://127.0.0.1:8740
环境变量：PAPERLAB_DEMO_PORT / PAPERLAB_OLLAMA / PAPERLAB_ADVISOR_MODEL
"""
import json
import os
import re
import time
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO = os.path.join(ROOT, "demo", "data")
PORT = int(os.environ.get("PAPERLAB_DEMO_PORT", "8740"))
OLLAMA = os.environ.get("PAPERLAB_OLLAMA", "http://127.0.0.1:11434")
ADVISOR_MODEL = os.environ.get("PAPERLAB_ADVISOR_MODEL", "qwen3:8b")

ADVICE_RANGE = {  # 白名单：路径 -> (下限, 上限)
    "signal.stop_pct": (0.5, 4.0), "signal.target_pct": (1.5, 8.0),
    "signal.trail_arm_pct": (0.8, 5.0), "signal.trail_lock_pct": (0.4, 2.5),
    "signal.daily_loss_halt_pct": (1.0, 4.0), "signal.momentum_pct": (0.1, 1.0),
    "signal.timeout_cycles": (60, 400), "signal.max_positions": (1, 8),
    "signal.max_trades_per_hour": (1, 12), "signal.min_entry_gap_sec": (30, 1800),
}
COGNITIVE_PATHS = {"signal.target_pct", "signal.momentum_pct", "signal.timeout_cycles",
                   "signal.max_trades_per_hour", "signal.min_entry_gap_sec"}
FREEZE_N = 118

AI_SYSTEM = """你是AI Trading Sandbox模拟交易研究框架的值班AI顾问，审阅一份模拟交易的统计摘要并给出参数建议。
纪律（必须遵守）：
1. 分层决策：风控参数（stop_pct/max_positions/daily_loss_halt_pct等）随时可建议；
   认知参数（target_pct/momentum_pct/timeout_cycles/频率类）在样本数<%d时处于冻结窗，
   不允许建议，只能提示继续收样。
2. 止损判定套路：满额止损多+离场后反弹 → 才考虑放宽止损；浅亏快出多+均值为负 → 是入场质量问题，
   应建议减仓/降频，而不是收紧止损（收紧会造成更多止损）。
3. 超时离场占比高 → 行情横盘或入场门槛过松，按冻结窗纪律处理。
4. 每条建议的"理由"必须以[风控]或[认知]开头，并引用一个数字依据。
5. 最多3条建议；0条建议+"继续收样"是合格输出。绝不建议接入真实资金或放大名义额度。
Schema: {"诊断":["..."],"建议":[{"参数":"白名单键","值":数字,"理由":"[风控]...","白话":"一句人话"}],"总结":"一句人话"}
白名单与范围: %s""" % (FREEZE_N, json.dumps({k: list(v) for k, v in ADVICE_RANGE.items()}, ensure_ascii=False))


def _load(name, default):
    try:
        with open(os.path.join(DEMO, name), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save(name, obj):
    with open(os.path.join(DEMO, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def digest():
    tr = _load("trades.json", {}).get("stocks", [])
    closed = [t for t in tr if t.get("status") == "closed" and t.get("pct") is not None]
    pcts = [t["pct"] for t in closed]
    n = len(closed)
    stops = [t for t in closed if "止损" in str(t.get("why_out", ""))]
    full = [t for t in stops if t["pct"] <= -1.0]
    shallow = [t for t in stops if t["pct"] > -1.0]
    tps = [t for t in closed if "止盈" in str(t.get("why_out", ""))]
    tos = [t for t in closed if "超时" in str(t.get("why_out", ""))]
    holds = []
    for t in closed:
        try:
            d0 = datetime.strptime(t["entry_ts"], "%Y-%m-%d %H:%M:%S")
            d1 = datetime.strptime(t["exit_ts"], "%Y-%m-%d %H:%M:%S")
            holds.append((d1 - d0).total_seconds() / 60)
        except Exception:
            pass
    return {"n": n,
            "winrate": round(100 * sum(1 for p in pcts if p > 0) / n, 1) if n else None,
            "mean_pct": round(sum(pcts) / n, 2) if n else None,
            "worst": min(pcts) if pcts else None,
            "best": max(pcts) if pcts else None,
            "stop_hits": len(stops), "stop_full_hits": len(full), "stop_shallow": len(shallow),
            "take_profit_hits": len(tps), "timeouts": len(tos),
            "avg_hold_min": round(sum(holds) / len(holds), 1) if holds else None,
            "key_params": {"stop_pct": 1.5, "target_pct": 3.0, "trail_arm_pct": 2.0,
                           "trail_lock_pct": 1.2, "max_positions": 5,
                           "max_trades_per_hour": 4, "timeout_cycles": 160},
            "cognitive_freeze": n < FREEZE_N}


def run_advisor():
    t0 = time.time()
    d = digest()
    user_msg = ("模拟盘统计：已平仓%d笔，胜率%s%%，均值%s%%，最差%s%%；"
                "满额止损%d次+浅亏快出%d次，止盈%d次，超时离场%d次；平均持有%s分钟。"
                "关键参数：止损1.5%%/止盈3.0%%/移动止盈2.0%%启用回撤1.2%%走/最多5仓/每小时4笔。"
                "认知冻结门槛=%d笔，当前%s。请按Schema输出JSON。" % (
                    d["n"], d["winrate"], d["mean_pct"], d["worst"],
                    d["stop_full_hits"], d["stop_shallow"], d["take_profit_hits"], d["timeouts"],
                    d["avg_hold_min"], FREEZE_N,
                    "在冻结窗内" if d["cognitive_freeze"] else "已过冻结窗"))
    body = json.dumps({"model": ADVISOR_MODEL, "messages": [
        {"role": "system", "content": AI_SYSTEM},
        {"role": "user", "content": user_msg}],
        "think": False, "format": "json", "stream": False,
        "options": {"temperature": 0.3}}).encode()
    req = urllib.request.Request(OLLAMA + "/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read()).get("message", {}).get("content", "{}")
    try:
        data = json.loads(data)
    except Exception:
        m = re.search(r"\{[\s\S]*\}", data)
        data = json.loads(m.group(0)) if m else {}
    params = _load("params.json", {}).get("stock_cfg", {}).get("signal", {})
    advice, seen = [], set()
    for a in (data.get("建议") or []):
        p, v = str(a.get("参数", "")), a.get("值")
        if p not in ADVICE_RANGE or not isinstance(v, (int, float)):
            continue
        lo, hi = ADVICE_RANGE[p]
        v = max(lo, min(hi, round(float(v), 3)))
        cur = params.get(p.split(".")[-1])
        if cur == v or (p, v) in seen:
            continue
        seen.add((p, v))
        advice.append({"path": p, "value": v,
                       "reason": str(a.get("理由", ""))[:200],
                       "plain": str(a.get("白话", ""))[:120]})
        if len(advice) >= 3:
            break
    diag = [str(x)[:300] for x in (data.get("诊断") or [])][:6]
    if d["n"] < FREEZE_N:
        dropped = [a for a in advice if a["path"] in COGNITIVE_PATHS]
        if dropped:
            advice = [a for a in advice if a["path"] not in COGNITIVE_PATHS]
            diag.append("样本n=%d<%d，按冻结窗纪律已过滤认知参数（%s）建议——继续收样。" % (
                d["n"], FREEZE_N, "、".join(sorted({a["path"].split(".")[-1] for a in dropped}))))
    return {"digest": d, "diag": diag, "advice": advice,
            "summary": str(data.get("总结", ""))[:200],
            "model": ADVISOR_MODEL, "took_s": round(time.time() - t0, 1)}


class H(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _json(self, obj, status=200):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/":
            with open(os.path.join(ROOT, "dashboard", "panel.html"), encoding="utf-8") as f:
                b = f.read().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
        elif u.path == "/api/snapshot":
            self._json(_load("snapshot.json", {}))
        elif u.path == "/api/trades":
            self._json(_load("trades.json", {"stocks": [], "futures": [], "shadows": []}))
        elif u.path == "/api/nodes":
            self._json(_load("nodes.json", {"nodes": []}))
        elif u.path == "/api/params":
            now = datetime.now()
            p = _load("params.json", {"stock_cfg": {}, "futures_cfg": {}})
            self._json({"stock_cfg": p.get("stock_cfg", {}), "futures_cfg": p.get("futures_cfg", {}),
                        "stock_open": 9 <= now.hour < 21, "now": now.strftime("%Y-%m-%d %H:%M:%S")})
        elif u.path == "/api/px":
            code = q.get("code", [""])[0]
            pts = [json.loads(L) for L in open(os.path.join(DEMO, "kline_%s_5m.jsonl" % code),
                                              encoding="utf-8")] if code else []
            self._json({"mode": "line", "series": [[p["t"], p["c"]] for p in pts],
                        "src": "合成演示数据"})
        elif u.path == "/api/kline":
            code = q.get("code", [""])[0]
            t0 = int(q.get("t0", ["0"])[0])
            t1 = int(q.get("t1", ["9999999999"])[0])
            bars = []
            for itv, f in (("5m", "kline_%s_5m.jsonl"), ("1d", "kline_%s_1d.jsonl")):
                path = os.path.join(DEMO, f % code)
                if not os.path.exists(path):
                    continue
                with open(path, encoding="utf-8") as fh:
                    rows = [json.loads(L) for L in fh]
                if itv == "5m":
                    rows = [r for r in rows if t0 <= r["t"] <= t1]
                else:
                    d0 = datetime.fromtimestamp(t0).strftime("%Y-%m-%d")
                    d1 = datetime.fromtimestamp(t1).strftime("%Y-%m-%d")
                    rows = [r for r in rows if d0 <= r["date"] <= d1]
                if len(rows) > len(bars):
                    bars = rows
                    interval = itv
            if bars and len(bars) >= 3:
                self._json({"mode": "candle", "bars": bars[:400], "interval": interval,
                            "src": "合成演示数据", "has_vol": True})
            else:
                self._json({"mode": "line", "series": [], "src": "合成演示数据"})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            body = {}
        if u.path == "/api/param_change":
            scope = body.get("scope", "stock")
            changes = body.get("changes", [])
            params = _load("params.json", {})
            cfg = params.setdefault("%s_cfg" % scope, {}).setdefault("signal", {})
            applied = []
            for c in changes:
                p = str(c.get("path", ""))
                key = p.split(".")[-1]
                if key not in {k.split(".")[-1] for k in ADVICE_RANGE} | {
                        "t1_enabled", "k1_enabled", "shadow_enabled", "entry_hour_max"}:
                    continue
                old = cfg.get(key)
                v = c.get("new")
                if isinstance(old, bool):
                    v = bool(v)
                elif isinstance(old, (int, float)) and isinstance(v, (int, float)):
                    v = type(old)(v)
                cfg[key] = v
                applied.append({"path": p, "old": old, "new": v})
            if not applied:
                self._json({"ok": False, "error": "无可识别参数"}, 400)
                return
            _save("params.json", params)
            title = "调整%d项参数：%s" % (len(applied), applied[0]["path"].split(".")[-1])
            if len(applied) == 1 and "old" in applied[0] and applied[0]["old"] is not None:
                title = "%s %s→%s" % (applied[0]["path"].split(".")[-1], applied[0]["old"], applied[0]["new"])
            nodes = _load("nodes.json", {"nodes": []})
            entry = {"ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "scope": scope,
                     "title": title, "plain": str(body.get("plain", ""))[:300],
                     "changes": applied, "source": body.get("source", "demo-panel")}
            nodes["nodes"].append(entry)
            _save("nodes.json", nodes)
            self._json({"ok": True, "entry": {"title": entry["title"]}})
        elif u.path == "/api/restart_daemon":
            self._json({"ok": True, "out": "演示模式：参数写盘即生效（每次请求实时重读），无需重启。"})
        elif u.path == "/api/ai_advisor":
            try:
                self._json(run_advisor())
            except Exception:
                self._json({"error": "本地Ollama未响应（%s）。AI体检为可选功能：安装Ollama并拉取 %s "
                                     "后即可使用，面板其余功能不受影响。" % (OLLAMA, ADVISOR_MODEL)})
        else:
            self._json({"error": "not found"}, 404)


if __name__ == "__main__":
    print("AI Trading Sandbox 演示面板 → http://127.0.0.1:%d  (Ctrl+C 退出)" % PORT)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
