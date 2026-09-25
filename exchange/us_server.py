# -*- coding: utf-8 -*-
"""美股虚拟交易所（T+0 / 按股 / 零佣金 / 可做空 / 10万虚拟USD）。

端口 8720，数据 data/usstock.db，行情：新浪 gb_（us_marketdata）。
API 与 A股交易所同构（/api/account|positions|order|trades|equity|pnl|halt|resume），
dashboard/index.html 直接复用。

启动：python3 exchange/us_server.py [--port 8720]
"""
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from us_marketdata import USMarketData, norm_us  # noqa: E402
from ledger import Ledger  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "us_config.json")
DEFAULTS = {"port": 8720, "initial_cash": 100000.0, "slippage_bps": 5.0,
            "margin_ratio": 0.5, "equity_interval_sec": 60}


def load_config():
    cfg = dict(DEFAULTS)
    if os.path.exists(CFG_PATH):
        try:
            with open(CFG_PATH, encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception as e:
            print("[us-config]", e)
    return cfg


class USExchange:
    def __init__(self):
        self.cfg = load_config()
        self.ledger = Ledger(os.path.join(ROOT, "data", "usstock.db"),
                             self.cfg["initial_cash"], t_plus_one=False)
        self.md = USMarketData()
        self.quote_cache = {}

    def market_open(self):
        """美东 9:30-16:00（用 UTC-4 近似 EDT；盘前盘后也算可交易——虚拟盘宽松）。"""
        t = time.gmtime(time.time() - 4 * 3600)
        hm = t.tm_hour * 60 + t.tm_min
        wd = t.tm_wday
        if wd >= 5:
            return False
        return (4 * 60) <= hm <= (20 * 60)  # 4:00-20:00 ET 含盘前后

    def fee(self, notional):
        return 0.0

    def valuation(self):
        acct = self.ledger.account()
        codes = [p["code"] for p in self.ledger.positions()]
        codes += [s["code"] for s in self.ledger.shorts()]
        quotes = self.md.quotes(codes) if codes else {}
        mv = sum(((quotes.get(p["code"]) or self.quote_cache.get(p["code"]) or {})
                  .get("price") or p["avg_cost"]) * p["shares"]
                 for p in self.ledger.positions())
        se = sum(((quotes.get(s["code"]) or self.quote_cache.get(s["code"]) or {})
                  .get("price") or s["avg_price"]) * s["shares"]
                 for s in self.ledger.shorts())
        for c, q in quotes.items():
            if q.get("price") is not None:
                self.quote_cache[c] = q
        return acct["cash"], mv, se, quotes

    def snapshot(self):
        cash, mv, se, _ = self.valuation()
        acct = self.ledger.account()
        total = cash + mv - se
        series = self.ledger.equity_series(5000)
        prev = None
        today_str = time.strftime("%Y-%m-%d")
        for pt in reversed(series):
            if pt["ts"][:10] < today_str:
                prev = pt["total"]
                break
        day = total - prev if prev is not None else total - acct["initial_cash"]
        return {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "cash": round(cash, 2),
                "market_value": round(mv, 2), "short_exposure": round(se, 2),
                "total_equity": round(total, 2), "initial_cash": acct["initial_cash"],
                "total_pnl": round(total - acct["initial_cash"], 2),
                "total_pnl_pct": round((total / acct["initial_cash"] - 1) * 100, 3),
                "day_pnl": round(day, 2), "halted": bool(acct["halted"]),
                "halt_reason": acct["halt_reason"] or "", "market_open": self.market_open()}

    def submit(self, body):
        action = str(body.get("action", "")).lower()
        if action not in ("buy", "sell", "short", "cover"):
            return False, {"error": "action 必须是 buy/sell/short/cover"}, 400
        code = norm_us(body.get("code"))
        if not code:
            return False, {"error": "code 无效（美股 ticker，如 AAPL）"}, 400
        try:
            shares = int(body.get("shares", 0))
        except (TypeError, ValueError):
            return False, {"error": "shares 必须是整数"}, 400
        if shares <= 0:
            return False, {"error": "shares 必须为正"}, 400
        reason = str(body.get("reason", ""))[:500]
        acct = self.ledger.account()
        if acct["halted"] and not body.get("force"):
            return False, {"error": "已熔断: %s" % acct["halt_reason"]}, 423
        q = self.md.quotes([code]).get(code) or self.quote_cache.get(code)
        if not q or q.get("price") is None:
            return False, {"error": "拿不到 %s 行情" % code}, 503
        self.quote_cache[code] = q
        slip = self.cfg["slippage_bps"] / 10000.0
        fill = q["price"] * (1 + slip if action in ("buy", "cover") else 1 - slip)
        fill = round(fill, 2)
        return self._execute(action, code, q.get("name") or code, shares, fill, reason)

    def _execute(self, action, code, name, shares, price, reason=""):
        acct = self.ledger.account()
        cash = acct["cash"]
        notional = price * shares
        fee = 0.0
        realized = 0.0
        if action == "buy":
            if cash < notional:
                return False, {"error": "现金不足：需 %.2f，可用 %.2f" % (notional, cash)}, 400
            cash_after = cash - notional
        elif action == "sell":
            pos = self.ledger.position(code)
            if not pos or pos["available"] < shares:
                return False, {"error": "可卖不足"}, 400
            realized = (price - pos["avg_cost"]) * shares
            cash_after = cash + notional
        elif action == "short":
            if cash < notional * self.cfg["margin_ratio"]:
                return False, {"error": "保证金不足（50%%）：需 %.2f，可用 %.2f"
                               % (notional * 0.5, cash)}, 400
            cash_after = cash + notional
        else:
            sp = self.ledger.short(code)
            if not sp or sp["shares"] < shares:
                return False, {"error": "空头仓位不足"}, 400
            realized = (sp["avg_price"] - price) * shares
            if cash < notional:
                return False, {"error": "现金不足：回补需 %.2f" % notional}, 400
            cash_after = cash - notional
        ts = self.ledger.execute(action, code, name, shares, price, fee,
                                 realized, cash_after, reason)
        cash2, mv, se, _ = self.valuation()
        total = self.ledger.add_equity(cash2, mv, se)
        return True, {"ts": ts, "action": action, "code": code, "name": name,
                      "shares": shares, "price": price, "fee": fee,
                      "realized": round(realized, 2), "cash_after": round(cash_after, 2),
                      "total_equity": round(total, 2)}, 200

    def pnl_groups(self, group="daily", limit=60):
        trades = self.ledger.trades(limit=5000)
        buckets = {}
        for t in trades:
            key = t["ts"][:10] if group == "daily" else t["ts"][:13]
            b = buckets.setdefault(key, {"realized": 0.0, "trades": 0})
            b["realized"] += t["realized"]
            b["trades"] += 1
        return [{"key": k, "realized": round(v["realized"], 2), "trades": v["trades"]}
                for k, v in sorted(buckets.items())][-limit:]

    def start_background(self):
        def equity_loop():
            while True:
                try:
                    cash, mv, se, _ = self.valuation()
                    self.ledger.add_equity(cash, mv, se)
                except Exception as e:
                    print("[us-equity]", e)
                time.sleep(self.cfg["equity_interval_sec"])
        threading.Thread(target=equity_loop, daemon=True).start()


def make_handler(ex):
    dashboard_path = os.path.join(ROOT, "dashboard", "index.html")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _json(self, obj, status=200):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

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
                    self._json({"status": "ok", "market": "US", "market_open": ex.market_open()})
                elif u.path == "/api/quotes":
                    syms = [norm_us(s) for s in (qs.get("codes", [""])[0] or "").split(",") if s.strip()]
                    quotes = ex.md.quotes([s for s in syms if s])
                    self._json({"quotes": list(quotes.values())})
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
                        longs.append({**p, "price": price,
                                      "unrealized": round((price - p["avg_cost"]) * p["shares"], 2),
                                      "unrealized_pct": round((price / p["avg_cost"] - 1) * 100, 2)
                                      if p["avg_cost"] else 0,
                                      "value": round(price * p["shares"], 2)})
                    shorts = []
                    for s in ex.ledger.shorts():
                        q = quotes.get(s["code"]) or ex.quote_cache.get(s["code"]) or {}
                        price = q.get("price") or s["avg_price"]
                        shorts.append({**s, "price": price,
                                       "unrealized": round((s["avg_price"] - price) * s["shares"], 2),
                                       "value": round(price * s["shares"], 2)})
                    self._json({"longs": longs, "shorts": shorts})
                elif u.path == "/api/trades":
                    self._json({"trades": ex.ledger.trades(int(qs.get("limit", ["100"])[0]))})
                elif u.path == "/api/equity":
                    self._json({"points": ex.ledger.equity_series(2000)})
                elif u.path == "/api/pnl":
                    self._json({"group": qs.get("group", ["daily"])[0],
                                "buckets": ex.pnl_groups(qs.get("group", ["daily"])[0])})
                elif u.path == "/api/halts":
                    self._json({"halts": ex.ledger.halt_history()})
                else:
                    self._json({"error": "not found"}, 404)
            except Exception as e:
                self._json({"error": "server error: %s" % e}, 500)

        def do_POST(self):
            u = urlparse(self.path)
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
            except Exception:
                body = {}
            try:
                if u.path == "/api/order":
                    ok, payload, status = ex.submit(body)
                    self._json(payload, status)
                elif u.path == "/api/halt":
                    ex.ledger.halt(body.get("reason") or "manual")
                    self._json({"ok": True})
                elif u.path == "/api/resume":
                    ex.ledger.resume()
                    self._json({"ok": True})
                else:
                    self._json({"error": "not found"}, 404)
            except Exception as e:
                self._json({"error": "server error: %s" % e}, 500)

    return Handler


def main():
    ex = USExchange()
    port = int(ex.cfg["port"])
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])
    ex.start_background()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(ex))
    print("[us-vstock] 美股虚拟交易所启动: http://127.0.0.1:%d" % port)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
