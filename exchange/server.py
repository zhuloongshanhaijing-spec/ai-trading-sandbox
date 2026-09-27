# -*- coding: utf-8 -*-
"""A股虚拟交易所主服务：撮合 + REST API + 面板静态页。仅标准库。

启动：python3 exchange/server.py [--port 8710]
规则（可在 exchange/config.json 覆盖）：
- 100股整手；市价单按实时价 ± slippage 成交（方向不利）；
- 佣金 万2.5 最低5元；印花税 卖出/融券卖出 0.05%；过户费 万0.1；
- 涨跌停 ±10%（创业板/科创 ±20%）外拒单；
- T+1（当日买入次日可卖）；做空=融券模拟，回补 T+0，保证金率 50%。
"""
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from marketdata import MarketData, norm_code, price_limit_pct  # noqa: E402
from ledger import Ledger  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CONFIG = {
    "port": 8710,
    "initial_cash": 1000000.0,
    "slippage_bps": 5.0,          # 0.05%
    "commission_rate": 0.00025,   # 万2.5
    "commission_min": 5.0,
    "stamp_tax": 0.0005,          # 卖出 0.05%
    "transfer_fee": 0.0001,       # 过户费 万0.1（双边）
    "lot": 100,
    "margin_ratio": 0.5,          # 做空保证金率
    "equity_interval_sec": 60,
    "order_check_interval_sec": 5,
    "t_plus_one": True,           # T+1；False=当日可卖（研究/演示用）
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception as e:
            print("[config] 读取失败，用默认:", e)
    return cfg


class Exchange:
    def __init__(self, db_path=None):
        self.cfg = load_config()
        db_path = db_path or os.path.join(ROOT, "data", "vstock.db")
        self.ledger = Ledger(db_path, self.cfg["initial_cash"],
                             self.cfg.get("t_plus_one", True))
        self.md = MarketData()
        from sources import SourceManager
        self.sm = SourceManager()  # 数据源注册表+换源闸门（见 docs/ADAPTER-SPEC.md）
        self.quote_cache = {}  # code -> quote（最近一次拿到的，含盘外）

    # ---------- 费用 ----------
    def fee(self, action, notional):
        c = self.cfg
        comm = max(c["commission_min"], notional * c["commission_rate"])
        stamp = notional * c["stamp_tax"] if action in ("sell", "short") else 0.0
        transfer = notional * c["transfer_fee"]
        return round(comm + stamp + transfer, 2)

    # ---------- 估值 ----------
    def valuation(self):
        """返回 (cash, market_value, short_exposure, quotes_used)"""
        acct = self.ledger.account()
        codes = [p["code"] for p in self.ledger.positions()]
        codes += [s["code"] for s in self.ledger.shorts()]
        mv = se = 0.0
        quotes = self.md.quotes(codes) if codes else {}
        for p in self.ledger.positions():
            q = quotes.get(p["code"]) or self.quote_cache.get(p["code"])
            price = (q or {}).get("price")
            if price is None:
                price = p["avg_cost"]  # 无行情时按成本估
            mv += price * p["shares"]
        for s in self.ledger.shorts():
            q = quotes.get(s["code"]) or self.quote_cache.get(s["code"])
            price = (q or {}).get("price")
            if price is None:
                price = s["avg_price"]
            se += price * s["shares"]
        for c, q in quotes.items():
            if q.get("price") is not None:
                self.quote_cache[c] = q
        return acct["cash"], mv, se, quotes

    def snapshot(self):
        cash, mv, se, _ = self.valuation()
        acct = self.ledger.account()
        total = cash + mv - se
        series = self.ledger.equity_series(5000)
        prev_day_total = None
        today_str = time.strftime("%Y-%m-%d")
        for pt in reversed(series):
            if pt["ts"][:10] < today_str:
                prev_day_total = pt["total"]
                break
        day_pnl = total - prev_day_total if prev_day_total is not None else total - acct["initial_cash"]
        return {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "cash": round(cash, 2),
            "market_value": round(mv, 2),
            "short_exposure": round(se, 2),
            "total_equity": round(total, 2),
            "initial_cash": acct["initial_cash"],
            "total_pnl": round(total - acct["initial_cash"], 2),
            "total_pnl_pct": round((total / acct["initial_cash"] - 1) * 100, 3),
            "day_pnl": round(day_pnl, 2),
            "halted": bool(acct["halted"]),
            "halt_reason": acct["halt_reason"] or "",
            "market_open": self.market_open(),
        }

    @staticmethod
    def market_open():
        t = time.localtime()
        if t.tm_wday >= 5:
            return False
        hm = t.tm_hour * 60 + t.tm_min
        return (9 * 60 + 30) <= hm <= (11 * 60 + 30) or (13 * 60) <= hm <= (15 * 60)

    # ---------- 下单 ----------
    def submit(self, body):
        """市价/限价下单。返回 (ok, payload, http_status)"""
        action = str(body.get("action", "")).lower()
        if action not in ("buy", "sell", "short", "cover"):
            return False, {"error": "action 必须是 buy/sell/short/cover"}, 400
        code = norm_code(body.get("code"))
        if not code:
            return False, {"error": "code 无效，示例 sh600519 / 600519"}, 400
        try:
            shares = int(body.get("shares", 0))
        except (TypeError, ValueError):
            return False, {"error": "shares 必须是整数"}, 400
        lot = int(self.cfg["lot"])
        if shares <= 0 or shares % lot:
            return False, {"error": "shares 必须是 %d 的正整数倍" % lot}, 400
        reason = str(body.get("reason", ""))[:500]
        otype = str(body.get("type", "market")).lower()

        acct = self.ledger.account()
        if acct["halted"] and not body.get("force"):
            return False, {"error": "账户已熔断暂停交易: %s（resume 后恢复）" % acct["halt_reason"]}, 423

        qs = self.md.quotes([code])
        q = qs.get(code) or self.quote_cache.get(code)
        if not q or q.get("price") is None:
            return False, {"error": "拿不到 %s 的行情（停牌或数据源故障）" % code}, 503
        self.quote_cache[code] = q
        name = q.get("name") or code

        if otype == "limit":
            try:
                lp = float(body.get("limit_price"))
            except (TypeError, ValueError):
                return False, {"error": "限价单必须提供 limit_price"}, 400
            oid = self.ledger.add_order(action, code, name, shares, lp, reason)
            return True, {"order_id": oid, "status": "open",
                          "msg": "限价单已挂出，等行情触价成交"}, 200

        fill = self._fill_price(action, q["price"])
        ok, payload, status = self._execute(action, code, name, shares, fill, reason)
        if ok:
            payload = dict(payload)
            payload["name"] = name
            payload["day_pct"] = q.get("pct")
        return ok, payload, status

    def _fill_price(self, action, price):
        slip = self.cfg["slippage_bps"] / 10000.0
        adverse = {"buy": 1 + slip, "cover": 1 + slip,
                   "sell": 1 - slip, "short": 1 - slip}[action]
        return round(price * adverse, 3)

    def _execute(self, action, code, name, shares, price, reason=""):
        """以给定价格执行（市价直接调用；限价触发也走这里）。"""
        acct = self.ledger.account()
        cash = acct["cash"]
        notional = price * shares
        # 涨跌停校验
        q = self.quote_cache.get(code) or {}
        prev = q.get("prev_close")
        if prev:
            lim = price_limit_pct(code)
            if price > prev * (1 + lim) + 1e-6 or price < prev * (1 - lim) - 1e-6:
                return False, {"error": "价格 %.3f 超出涨跌停范围（昨收 %.3f, ±%.0f%%）"
                               % (price, prev, lim * 100)}, 400
        fee = self.fee(action, notional)
        realized = 0.0
        if action == "buy":
            need = notional + fee
            if cash < need:
                return False, {"error": "现金不足：需 %.2f（含费），可用 %.2f" % (need, cash)}, 400
            cash_after = cash - need
        elif action == "sell":
            pos = self.ledger.position(code)
            if not pos or pos["available"] < shares:
                avail = pos["available"] if pos else 0
                return False, {"error": "可卖不足：T+1 规则下当日可用 %d 股" % avail}, 400
            realized = (price - pos["avg_cost"]) * shares - fee
            cash_after = cash + notional - fee
        elif action == "short":
            margin = notional * self.cfg["margin_ratio"]
            if cash < margin + fee:
                return False, {"error": "保证金不足：做空需保证金 %.2f + 费用 %.2f，可用 %.2f"
                               % (margin, fee, cash)}, 400
            realized = 0.0
            cash_after = cash + notional - fee
        else:  # cover
            sp = self.ledger.short(code)
            if not sp or sp["shares"] < shares:
                have = sp["shares"] if sp else 0
                return False, {"error": "空头仓位不足：当前 %d 股" % have}, 400
            realized = (sp["avg_price"] - price) * shares - fee
            need = notional + fee
            if cash < need:
                return False, {"error": "现金不足：回补需 %.2f（含费），可用 %.2f" % (need, cash)}, 400
            cash_after = cash - need
        ts = self.ledger.execute(action, code, name, shares, price, fee, realized, cash_after, reason)
        # 成交后立即记一个净值点
        cash2, mv, se, _ = self.valuation()
        total = self.ledger.add_equity(cash2, mv, se)
        return True, {
            "ts": ts, "action": action, "code": code, "name": name,
            "shares": shares, "price": price, "fee": fee,
            "realized": round(realized, 2), "cash_after": round(cash_after, 2),
            "total_equity": round(total, 2),
        }, 200

    # ---------- 盈亏分组 ----------
    def pnl_groups(self, group="daily", limit=60):
        trades = self.ledger.trades(limit=5000)
        buckets = {}
        for t in trades:
            key = t["ts"][:10] if group == "daily" else t["ts"][:13]
            b = buckets.setdefault(key, {"realized": 0.0, "fees": 0.0, "trades": 0,
                                         "buy_amt": 0.0, "sell_amt": 0.0})
            b["realized"] += t["realized"]
            b["fees"] += t["fee"]
            b["trades"] += 1
            if t["action"] in ("buy", "cover"):
                b["buy_amt"] += t["price"] * t["shares"]
            else:
                b["sell_amt"] += t["price"] * t["shares"]
        out = [{"key": k, **{x: round(v, 2) if isinstance(v, float) else v
                             for x, v in buckets[k].items()}}
               for k in sorted(buckets)][-limit:]
        return out

    # ---------- 后台线程 ----------
    def start_background(self):
        def equity_loop():
            while True:
                try:
                    cash, mv, se, _ = self.valuation()
                    self.ledger.add_equity(cash, mv, se)
                except Exception as e:
                    print("[equity]", e)
                time.sleep(self.cfg["equity_interval_sec"])

        def settle_loop():
            while True:
                try:
                    self.ledger.settle_t1()
                except Exception as e:
                    print("[settle]", e)
                time.sleep(60)

        def order_loop():
            while True:
                try:
                    self._check_orders()
                except Exception as e:
                    print("[orders]", e)
                time.sleep(self.cfg["order_check_interval_sec"])

        for target in (equity_loop, settle_loop, order_loop):
            threading.Thread(target=target, daemon=True).start()

    def _check_orders(self):
        opens = self.ledger.open_orders()
        if not opens:
            return
        codes = list({o["code"] for o in opens})
        quotes = self.md.quotes(codes)
        for o in opens:
            q = quotes.get(o["code"]) or self.quote_cache.get(o["code"])
            if not q or q.get("price") is None:
                continue
            self.quote_cache[o["code"]] = q
            p = q["price"]
            hit = False
            if o["action"] in ("buy", "cover") and p <= o["limit_price"]:
                hit = True
            if o["action"] in ("sell", "short") and p >= o["limit_price"]:
                hit = True
            if not hit:
                continue
            fill = self._fill_price(o["action"], p)
            ok, payload, _ = self._execute(o["action"], o["code"], o["name"] or "",
                                           o["shares"], fill, o["reason"] or "limit")
            self.ledger.finish_order(o["id"], "filled" if ok else "rejected", fill)
            if not ok:
                print("[order %d] 触价但执行失败: %s" % (o["id"], payload.get("error")))


# ---------------- HTTP ----------------
def make_handler(ex):
    dashboard_path = os.path.join(ROOT, "dashboard", "index.html")
    _placeholder = ("<!doctype html><meta charset=utf-8><title>AI Trading Sandbox Exchange</title>"
                    "<body style='font-family:sans-serif;max-width:640px;margin:60px auto'>"
                    "<h2>AI Trading Sandbox 虚拟交易所运行中</h2><p>REST API：<code>/api/quote?code=sh600519</code> "
                    "<code>/api/account</code> <code>/api/order</code> 等，见 README。</p>"
                    "<p>可视化面板请运行 <code>python3 dashboard/demo_server.py</code>。</p></body>").encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # 安静模式，日志走文件

        def _json(self, obj, status=200):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0:
                return {}
            try:
                return json.loads(self.rfile.read(n).decode("utf-8"))
            except Exception:
                return {}

        def do_GET(self):
            u = urlparse(self.path)
            qs = parse_qs(u.query)
            try:
                if u.path in ("/", "/dashboard"):
                    with open(dashboard_path, "rb") as f:
                        body = f.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif u.path == "/api/health":
                    self._json({"status": "ok", "market_open": ex.market_open(),
                                **{k: ex.snapshot()[k] for k in ("halted", "halt_reason")}})
                elif u.path == "/api/quotes":
                    codes = (qs.get("codes", [""])[0] or "").split(",")
                    src = ex.sm.get()
                    if ex.sm.current != "cn" and src is not None:
                        quotes = src.quotes([c.strip() for c in codes if c.strip()])
                        self._json({"quotes": list(quotes.values()),
                                    "source": ex.sm.current})
                    else:
                        codes = [norm_code(c) for c in codes if c.strip()]
                        quotes = ex.md.quotes([c for c in codes if c])
                        self._json({"quotes": list(quotes.values()) if quotes else [],
                                    "source": "cn"})
                elif u.path == "/api/kline":
                    code_raw = qs.get("code", [""])[0]
                    limit = int(qs.get("limit", ["120"])[0])
                    src = ex.sm.get()
                    if ex.sm.current != "cn" and src is not None:
                        rows = src.kline(code_raw, limit)
                        self._json({"code": code_raw,
                                    "bars": rows[-limit:] if rows else [],
                                    "source": ex.sm.current})
                    else:
                        code = norm_code(code_raw)
                        if not code:
                            self._json({"error": "code 无效"}, 400)
                        else:
                            rows = ex.md.kline(code, limit)
                            self._json({"code": code,
                                        "bars": rows[-limit:] if rows else [],
                                        "source": "cn"})
                elif u.path == "/api/search":
                    q = qs.get("q", [""])[0]
                    self._json({"results": ex.md.search(q, 10) if q else []})
                elif u.path == "/api/account":
                    snap = ex.snapshot()
                    snap["positions_count"] = len(ex.ledger.positions())
                    snap["short_count"] = len(ex.ledger.shorts())
                    self._json(snap)
                elif u.path == "/api/positions":
                    cash, mv, se, quotes = ex.valuation()
                    longs = []
                    for p in ex.ledger.positions():
                        q = quotes.get(p["code"]) or ex.quote_cache.get(p["code"]) or {}
                        price = q.get("price") or p["avg_cost"]
                        upnl = (price - p["avg_cost"]) * p["shares"]
                        longs.append({**p, "price": price,
                                      "unrealized": round(upnl, 2),
                                      "unrealized_pct": round((price / p["avg_cost"] - 1) * 100, 2)
                                      if p["avg_cost"] else 0.0,
                                      "value": round(price * p["shares"], 2)})
                    shorts = []
                    for s in ex.ledger.shorts():
                        q = quotes.get(s["code"]) or ex.quote_cache.get(s["code"]) or {}
                        price = q.get("price") or s["avg_price"]
                        upnl = (s["avg_price"] - price) * s["shares"]
                        shorts.append({**s, "price": price,
                                       "unrealized": round(upnl, 2),
                                       "value": round(price * s["shares"], 2)})
                    self._json({"longs": longs, "shorts": shorts})
                elif u.path == "/api/trades":
                    limit = int(qs.get("limit", ["100"])[0])
                    self._json({"trades": ex.ledger.trades(limit)})
                elif u.path == "/api/orders":
                    self._json({"orders": ex.ledger.orders(100)})
                elif u.path == "/api/equity":
                    pts = ex.ledger.equity_series(2000)
                    self._json({"points": pts})
                elif u.path == "/api/pnl":
                    group = qs.get("group", ["daily"])[0]
                    self._json({"group": group, "buckets": ex.pnl_groups(group)})
                elif u.path == "/api/halts":
                    self._json({"halts": ex.ledger.halt_history()})
                elif u.path == "/api/sources":
                    self._json(ex.sm.status())
                elif u.path == "/api/sources/eras":
                    self._json({"eras": ex.sm.eras()})
                elif u.path == "/api/source/positions":
                    src = ex.sm.get()
                    if src is None or not src.capabilities().get("trading"):
                        self._json({"error": "当前源不支持交易原语"}, 400)
                    else:
                        self._json({"positions": src.positions(),
                                    "source": ex.sm.current})
                elif u.path == "/api/source/account":
                    src = ex.sm.get()
                    if src is None or not src.capabilities().get("trading"):
                        self._json({"error": "当前源不支持交易原语"}, 400)
                    else:
                        self._json({**src.account(), "source": ex.sm.current})
                else:
                    self._json({"error": "not found"}, 404)
            except Exception as e:
                self._json({"error": "server error: %s" % e}, 500)

        def do_POST(self):
            u = urlparse(self.path)
            body = self._read_body()
            try:
                if u.path == "/api/order":
                    ok, payload, status = ex.submit(body)
                    self._json(payload, status)
                elif u.path == "/api/orders/cancel":
                    oid = int(body.get("id", 0))
                    ex.ledger.finish_order(oid, "cancelled")
                    self._json({"ok": True, "id": oid})
                elif u.path == "/api/halt":
                    ex.ledger.halt(body.get("reason") or "manual", body.get("detail", ""))
                    self._json({"ok": True, "halted": True})
                elif u.path == "/api/resume":
                    ex.ledger.resume()
                    self._json({"ok": True, "halted": False})
                elif u.path == "/api/settle":
                    n = ex.ledger.settle_t1()
                    self._json({"ok": True, "settled_shares": n})
                elif u.path == "/api/source/switch":
                    target = str(body.get("target", ""))

                    def _acct_view():
                        return {"positions": len(ex.ledger.positions())
                                + len(ex.ledger.shorts()),
                                "open_orders": len(ex.ledger.open_orders())}
                    r = ex.sm.switch(target, _acct_view)
                    self._json(r, 200 if r.get("ok") else 409)
                elif u.path == "/api/source/order":
                    src = ex.sm.get()
                    if src is None or not src.capabilities().get("trading"):
                        self._json({"error": "当前源不支持交易原语（trading=False）"}, 400)
                    else:
                        self._json(src.place_order(body.get("side"), body.get("code"),
                                                   body.get("shares"), body.get("price")))
                else:
                    self._json({"error": "not found"}, 404)
            except Exception as e:
                self._json({"error": "server error: %s" % e}, 500)

    return Handler


def main():
    ex = Exchange()
    port = int(ex.cfg["port"])
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])
    ex.start_background()
    ex.ledger.settle_t1()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(ex))
    print("[vstock] 虚拟交易所启动: http://127.0.0.1:%d  (面板: /  API: /api/*)" % port)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
