# 变更记录（Changelog）

本项目遵循[保持变更记录](https://keepachangelog.com/zh-CN/1.1.0/)惯例；
版本号与发布节奏由维护者决定（见 [COMMERCIAL-LICENSE.md](COMMERCIAL-LICENSE.md) 的商业边界）。

## [0.2.0] — 2026-09-27

### 新增
- **多市场数据源架构**：`MarketSource` 显式契约（`exchange/sources.py`）——能力声明
  `affects_price` / `funds_model` / `trading`，内置 A股/美股/港股/国内期货 四源注册表。
- **港股行情网关** `exchange/hk_marketdata.py`（实时报价 + 日K，纯标准库，带缓存）。
- **国内期货行情网关** `exchange/fut_marketdata.py`（主连合约实时含持仓量/结算价 + 日K，
  支持中文别名）。
- **自定义适配器机制**：`PAPERLAB_ADAPTER` 环境变量或运行时 API 加载用户自己的数据源；
  契约规格见 `docs/ADAPTER-SPEC.md`（含责任边界条款）。
- **示例交易型源** `examples/adapters/tiny_exchange_adapter.py`：零依赖迷你交易所，
  订单真实推动价格（演示 `affects_price=true` 的完整闭环）。
- **换源闸门与净值断代**：任何数据源切换前必须全部平仓撤单（硬约束）；
  每次切换写入断代记录 `data/source_eras.jsonl`，统计按断代分段、永不跨源拼接。
- 交易所 REST API 新增：`GET /api/sources`、`GET /api/sources/eras`、
  `POST /api/source/switch`、`POST /api/source/order`、
  `GET /api/source/positions`、`GET /api/source/account`；
  `/api/quotes`、`/api/kline` 按当前源自动代理。
- `exchange/config.json` 支持 `t_plus_one` 开关（默认 `true` 保持A股真实规则；
  `false` 供研究/演示当日可卖）。
- **离线单元测试套件** `tests/`（不依赖网络）：符号规范化、换源闸门、
  示例源订单冲击与禁透支、账本T+1结算。
- 本变更记录文件。

### 变更
- README 新增"两种体验模式"章节（基础模式 vs 完整模式对照）与责任边界说明。
- THIRD_PARTY_NOTICES.md 补充港股K线（腾讯）与国内期货（新浪）数据来源条目。
- dashboard 演示面板新增只读 `GET /api/sources`。

### 修复
- `.gitignore` 补 `exchange/config.json`（用户本地覆盖配置，不应入库）——
  使 SECURITY.md"本地配置已被 .gitignore 排除"的声明成立。

## [0.1.0] — 2026-09-25

首个公开版本（source-available，PolyForm Noncommercial 1.0.0）。

- 本地虚拟交易所：A股撮合模拟（100股整手/滑点/佣金印花税/涨跌停/T+1/融券做空）
  与美股模拟，行情走公开接口，纯标准库，REST API。
- 可视化演示面板（五页路由：K线病历卡/参数版本时间线/双模式控制台）。
- 决策与参数纪律工具（参数变更账本 + 假设卡自动验收）。
- 确定性合成演示数据（固定种子，无真实市场数据）。
- 本地AI体检（可选，仅建议+人工确认，AI不在交易回路）。
- 数据接入边界声明：不含任何第三方平台账号/数据/客户端/抓取代码/连接教程。
