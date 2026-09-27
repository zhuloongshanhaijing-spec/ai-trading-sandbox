# 数据源适配器规格（MarketSource Adapter Spec）

> 本文档只定义**技术契约**：一个数据源模块需要实现什么，才能被本框架当作行情/交易源加载。
> 本框架不附带、不指认、不提供任何第三方在线服务的适配器——接入什么由你决定，
> **合规责任由接入者自负**：你必须自行确保对所接入的服务拥有合法访问权限，并遵守其服务条款。

## 1. 两种数据源形态

| 能力 | 只读行情源 | 交易型源 |
|---|---|---|
| `affects_price` | `False`（你的操作不影响价格） | `True`（你的委托会被源侧撮合、推动价格） |
| `funds_model` | `local`（本地模拟撮合，初始资金可配） | `platform`（资金/持仓由源侧账户决定，本框架只读上报） |
| `trading` | `False` | `True`（须实现交易原语） |

体验差异：只读行情源 + 本地撮合 = "行情真实、成交模拟"；交易型源 = 完整闭环
（真实对手方、真实成交、真实账户权益变化）。

## 2. 契约（Python）

实现 `exchange/sources.py` 中的 `MarketSource`（或实现同名成员的等价对象），必须提供：

```python
class MySource(MarketSource):
    id = "my"                      # 短标识（用于换源API）
    label = "我的数据源"            # 展示名

    def capabilities(self):
        return {"affects_price": True,      # 见上表
                "funds_model": "platform",
                "trading": True,
                "sessions": "交易时段说明（展示用）"}

    # ---- 行情（必选）----
    def quotes(self, codes) -> dict:
        """批量实时报价；返回 {code: {code,name,price,pct,change,open,high,low,
        prev_close,volume,amount,ts}}。字段与 exchange/marketdata.py 返回同构。"""

    def kline(self, code, limit=120) -> list:
        """日K升序列表 [{date,open,close,high,low,volume,amount,pct}]；无数据返回 []。"""

    # ---- 交易（仅 trading=True 必选）----
    def place_order(self, side, code, shares, price=None) -> dict:
        """side∈{buy,sell,short}；返回 {ok, oid, price, ...} 或 {ok:False, error}。"""

    def cancel_order(self, oid) -> dict:   # {ok: ...}
    def positions(self) -> list:           # [{code,name,shares,price,market_value}]
    def account(self) -> dict:             # {cash,market_value,total,...}
```

模块须暴露 `SOURCE = MySource()`（或 `source=`）实例。线程安全自负（quotes 可能被并发调用）。

## 3. 加载方式

```bash
# 方式一：环境变量指向你的适配器文件
PAPERLAB_ADAPTER=/path/to/my_adapter.py python3 exchange/server.py

# 方式二：运行中经 API 换源（先满足清仓闸门）
curl -X POST localhost:8710/api/source/switch -d '{"target":"custom"}'
```

加载后：`/api/sources` 列出能力；`/api/quotes`、`/api/kline` 自动代理当前源；
交易型源经 `/api/source/order|positions|account` 访问。

## 4. 换源闸门与净值断代（硬语义）

- **换源前必须全部平仓并撤单**——闸门拒绝任何带持仓/挂单的切换
  （不同源的价格与资金语义不可混算，这不是建议而是硬约束）；
- 每次成功切换写入一条**断代记录（era）**：`data/source_eras.jsonl`；
- 净值曲线、胜率、回撤等统计**按断代分段**，永不跨源拼接。

## 5. 参考实现

- 只读行情源：`exchange/hk_marketdata.py` / `fut_marketdata.py`（内置港股/期货网关）
- 交易型源：`examples/adapters/tiny_exchange_adapter.py`
  （零依赖迷你交易所：订单真实推动价格、内置账户、7x24，可直接跑通全部API）

## 6. 责任与边界

- 你接入的任何服务，其数据权利与使用条款**归该服务所有者**；本框架不缓存、不再分发其数据；
- 请在适配器内做好限流与错误处理；对源侧造成的任何影响由接入者负责；
- 若你打算公开分发你的适配器，请自行先取得相关服务方的许可。
