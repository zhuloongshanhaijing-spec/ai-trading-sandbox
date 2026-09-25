# -*- coding: utf-8 -*-
"""账本层：SQLite 持久化（账户/持仓/做空/成交/挂单/净值曲线/熔断日志）。

约定：
- 多头持仓：shares 总数、available 可卖（T+1）、pending 当日买入待结算；
- 做空（融券模拟）：shares、avg_price 入场价；回补 T+0；
- cash 含做空入场收到的现金；总权益 = cash + 多头市值 - 空头市值。
"""
import os
import sqlite3
import threading
from datetime import datetime


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today():
    return datetime.now().strftime("%Y-%m-%d")


SCHEMA = """
CREATE TABLE IF NOT EXISTS account(
  id INTEGER PRIMARY KEY CHECK(id=1),
  initial_cash REAL NOT NULL,
  cash REAL NOT NULL,
  halted INTEGER NOT NULL DEFAULT 0,
  halt_reason TEXT DEFAULT '',
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS positions(
  code TEXT PRIMARY KEY,
  name TEXT NOT NULL DEFAULT '',
  shares INTEGER NOT NULL DEFAULT 0,
  available INTEGER NOT NULL DEFAULT 0,
  avg_cost REAL NOT NULL DEFAULT 0,
  pending INTEGER NOT NULL DEFAULT 0,
  pending_date TEXT,
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS short_positions(
  code TEXT PRIMARY KEY,
  name TEXT NOT NULL DEFAULT '',
  shares INTEGER NOT NULL DEFAULT 0,
  avg_price REAL NOT NULL DEFAULT 0,
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS trades(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  action TEXT NOT NULL,
  code TEXT NOT NULL,
  name TEXT DEFAULT '',
  shares INTEGER NOT NULL,
  price REAL NOT NULL,
  fee REAL NOT NULL,
  realized REAL NOT NULL DEFAULT 0,
  cash_after REAL NOT NULL,
  reason TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS orders(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  action TEXT NOT NULL,
  code TEXT NOT NULL,
  name TEXT DEFAULT '',
  shares INTEGER NOT NULL,
  limit_price REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  filled_price REAL,
  reason TEXT DEFAULT '',
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS equity_points(
  ts TEXT NOT NULL,
  cash REAL NOT NULL,
  market_value REAL NOT NULL,
  short_exposure REAL NOT NULL,
  total REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_equity_ts ON equity_points(ts);
CREATE TABLE IF NOT EXISTS halt_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  reason TEXT NOT NULL,
  detail TEXT DEFAULT ''
);
"""


class Ledger:
    def __init__(self, db_path, initial_cash=1000000.0, t_plus_one=True):
        self.t_plus_one = t_plus_one
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.executescript(SCHEMA)
            row = self.conn.execute("SELECT * FROM account WHERE id=1").fetchone()
            if not row:
                self.conn.execute(
                    "INSERT INTO account(id, initial_cash, cash, created_at) VALUES(1,?,?,?)",
                    (initial_cash, initial_cash, now()))
            self.conn.commit()

    # ---------------- 账户 ----------------
    def account(self):
        with self.lock:
            row = self.conn.execute("SELECT * FROM account WHERE id=1").fetchone()
            return dict(row)

    def set_cash(self, cash):
        with self.lock:
            self.conn.execute("UPDATE account SET cash=? WHERE id=1", (cash,))
            self.conn.commit()

    def halt(self, reason, detail=""):
        with self.lock:
            self.conn.execute("UPDATE account SET halted=1, halt_reason=? WHERE id=1", (reason,))
            self.conn.execute("INSERT INTO halt_log(ts, reason, detail) VALUES(?,?,?)",
                              (now(), reason, detail))
            self.conn.commit()

    def resume(self):
        with self.lock:
            self.conn.execute("UPDATE account SET halted=0, halt_reason='' WHERE id=1")
            self.conn.commit()

    def halt_history(self, limit=20):
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM halt_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            return [dict(r) for r in rows]

    # ---------------- T+1 结算 ----------------
    def settle_t1(self):
        """把 pending_date < 今日 的当日买入转入 available。返回结算数量。"""
        n = 0
        with self.lock:
            rows = self.conn.execute(
                "SELECT code, pending, pending_date FROM positions "
                "WHERE pending > 0 AND pending_date IS NOT NULL").fetchall()
            for r in rows:
                if r["pending_date"] < today():
                    self.conn.execute(
                        "UPDATE positions SET available=available+?, pending=0, pending_date=NULL, "
                        "updated_at=? WHERE code=?", (r["pending"], now(), r["code"]))
                    n += r["pending"]
            self.conn.commit()
        return n

    # ---------------- 持仓 ----------------
    def positions(self):
        with self.lock:
            rows = self.conn.execute("SELECT * FROM positions WHERE shares > 0").fetchall()
            return [dict(r) for r in rows]

    def position(self, code):
        with self.lock:
            row = self.conn.execute("SELECT * FROM positions WHERE code=?", (code,)).fetchone()
            return dict(row) if row else None

    def shorts(self):
        with self.lock:
            rows = self.conn.execute("SELECT * FROM short_positions WHERE shares > 0").fetchall()
            return [dict(r) for r in rows]

    def short(self, code):
        with self.lock:
            row = self.conn.execute(
                "SELECT * FROM short_positions WHERE code=?", (code,)).fetchone()
            return dict(row) if row else None

    # ---------------- 成交 ----------------
    def trades(self, limit=100, code=None):
        q = "SELECT * FROM trades"
        args = []
        if code:
            q += " WHERE code=?"
            args.append(code)
        q += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self.lock:
            rows = self.conn.execute(q, args).fetchall()
            return [dict(r) for r in rows]

    def execute(self, action, code, name, shares, price, fee, realized, cash_after, reason=""):
        """原子执行一笔成交（调用方已做校验与现金计算）。"""
        ts = now()
        with self.lock:
            self.conn.execute(
                "INSERT INTO trades(ts, action, code, name, shares, price, fee, realized,"
                " cash_after, reason) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (ts, action, code, name, shares, price, fee, realized, cash_after, reason))
            if action == "buy":
                row = self.conn.execute(
                    "SELECT shares, avg_cost FROM positions WHERE code=?", (code,)).fetchone()
                if row:
                    old_cost = row["shares"] * row["avg_cost"]
                    new_shares = row["shares"] + shares
                    avg = (old_cost + shares * price + fee) / new_shares
                    if self.t_plus_one:
                        self.conn.execute(
                            "UPDATE positions SET shares=?, avg_cost=?, pending=pending+?,"
                            " pending_date=?, updated_at=? WHERE code=?",
                            (new_shares, avg, shares, today(), ts, code))
                    else:
                        self.conn.execute(
                            "UPDATE positions SET shares=?, available=available+?, avg_cost=?,"
                            " updated_at=? WHERE code=?",
                            (new_shares, shares, avg, ts, code))
                else:
                    avg = (shares * price + fee) / shares
                    if self.t_plus_one:
                        self.conn.execute(
                            "INSERT INTO positions(code, name, shares, available, avg_cost, pending,"
                            " pending_date, updated_at) VALUES(?,?,?,0,?,?,?,?)",
                            (code, name, shares, avg, shares, today(), ts))
                    else:
                        self.conn.execute(
                            "INSERT INTO positions(code, name, shares, available, avg_cost, pending,"
                            " pending_date, updated_at) VALUES(?,?,?,?,?,0,NULL,?)",
                            (code, name, shares, shares, avg, ts))
            elif action == "sell":
                row = self.conn.execute(
                    "SELECT shares FROM positions WHERE code=?", (code,)).fetchone()
                left = (row["shares"] if row else 0) - shares
                if left > 0:
                    self.conn.execute(
                        "UPDATE positions SET shares=?, available=available-?, updated_at=?"
                        " WHERE code=?", (left, shares, ts, code))
                else:
                    self.conn.execute("DELETE FROM positions WHERE code=?", (code,))
            elif action == "short":
                row = self.conn.execute(
                    "SELECT shares, avg_price FROM short_positions WHERE code=?",
                    (code,)).fetchone()
                if row:
                    old = row["shares"] * row["avg_price"]
                    new_shares = row["shares"] + shares
                    avg = (old + shares * price) / new_shares
                    self.conn.execute(
                        "UPDATE short_positions SET shares=?, avg_price=?, updated_at=?"
                        " WHERE code=?", (new_shares, avg, ts, code))
                else:
                    self.conn.execute(
                        "INSERT INTO short_positions(code, name, shares, avg_price, updated_at)"
                        " VALUES(?,?,?,?,?)", (code, name, shares, price, ts))
            elif action == "cover":
                row = self.conn.execute(
                    "SELECT shares FROM short_positions WHERE code=?", (code,)).fetchone()
                left = (row["shares"] if row else 0) - shares
                if left > 0:
                    self.conn.execute(
                        "UPDATE short_positions SET shares=?, updated_at=? WHERE code=?",
                        (left, ts, code))
                else:
                    self.conn.execute("DELETE FROM short_positions WHERE code=?", (code,))
            self.conn.execute("UPDATE account SET cash=? WHERE id=1", (cash_after,))
            self.conn.commit()
        return ts

    # ---------------- 挂单 ----------------
    def open_orders(self):
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM orders WHERE status='open' ORDER BY id").fetchall()
            return [dict(r) for r in rows]

    def add_order(self, action, code, name, shares, limit_price, reason=""):
        with self.lock:
            cur = self.conn.execute(
                "INSERT INTO orders(ts, action, code, name, shares, limit_price, status, reason)"
                " VALUES(?,?,?,?,?,?, 'open', ?)",
                (now(), action, code, name, shares, limit_price, reason))
            self.conn.commit()
            return cur.lastrowid

    def finish_order(self, oid, status, filled_price=None):
        with self.lock:
            self.conn.execute(
                "UPDATE orders SET status=?, filled_price=?, updated_at=? WHERE id=?",
                (status, filled_price, now(), oid))
            self.conn.commit()

    def orders(self, limit=50):
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM orders ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            return [dict(r) for r in rows]

    # ---------------- 净值 ----------------
    def add_equity(self, cash, market_value, short_exposure):
        total = cash + market_value - short_exposure
        with self.lock:
            # 同一秒去重
            self.conn.execute("DELETE FROM equity_points WHERE ts=?", (now(),))
            self.conn.execute(
                "INSERT INTO equity_points(ts, cash, market_value, short_exposure, total)"
                " VALUES(?,?,?,?,?)", (now(), cash, market_value, short_exposure, total))
            self.conn.commit()
        return total

    def equity_series(self, limit=2000):
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM (SELECT * FROM equity_points ORDER BY ts DESC LIMIT ?)"
                " ORDER BY ts", (limit,)).fetchall()
            return [dict(r) for r in rows]
