# AI Trading Sandbox · AI 炒股实验场

> 一个让用户与 AI 一起**观察、调参、复盘并体验模拟交易过程**的本地研究框架。
> 默认使用**合成演示数据**或用户自行接入的**合法数据源**；不接入真实资金，不具备真实下单能力。

**这是实验性研究软件，不构成任何投资建议。**

## ✨ 功能

- **虚拟交易所**（`exchange/`，纯标准库）：A股撮合模拟（100股整手/滑点/佣金印花税/涨跌停/T+1/融券做空）与美股模拟（T+0/零佣/可做空），行情走公开接口（东财/新浪），自带 REST API。
- **决策与参数纪律工具**（`tools/`）：
  - `config_change.py` — 参数变更账本：任何调参先落一个"版本节点"，可追溯每次改动时的动机与前后值；
  - `card_check.py` — 预注册假设卡自动验收：把"今天预期的纪律"逐条对账成 PASS/FAIL。
- **可视化面板**（`dashboard/`）：五页路由（今日/历史/参数版本/控制台/详情）——K线病历卡（每笔交易嵌在走势上）、参数版本时间线（每个版本节点内嵌期间全部交易与统计）、双模式控制台（小白三旋钮联动写参 / 高玩全参数）。
- **本地AI体检**（可选）：面板"控制台 → AI体检卡"把交易统计摘要交给**本地 Ollama**（默认 `qwen3:8b`）分析，输出带白名单硬校验的参数建议——**仅建议，人工确认后**才写入参数账本；AI 永不直接改参、更不在交易回路里。服务端强制"认知参数冻结窗"（样本<118 笔时过滤对止盈/门槛/频率类参数的建议）。

## 🚀 快速开始（macOS + Python 3.11）

```bash
python3 demo/generate_demo_data.py   # 生成确定性合成演示数据（固定种子，无真实市场数据）
python3 dashboard/demo_server.py     # 启动演示面板 → http://127.0.0.1:8740
```

打开面板后：今日页看实时心跳与入场闸门；历史页看每笔交易的K线病历卡；**参数版本页**看"每次改参数前后的成绩对照"；控制台页拧旋钮（会真实写入演示参数账本并新增版本节点）。

可选：AI 体检需本地 [Ollama](https://ollama.com)（`ollama pull qwen3:8b`）。没有 Ollama 时面板其余功能完全不受影响。

虚拟交易所（可选，独立于演示面板）：

```bash
python3 exchange/server.py --port 8710    # A股模拟撮合 + REST API
python3 exchange/us_server.py --port 8720 # 美股模拟
```

工具链示例（读合成数据，可换成你自己的数据源）：

```bash
python3 tools/config_change.py list
python3 tools/card_check.py "$(date +%F)"
```

首版不包含自动选股模型或历史因子回测工具：它们需要独立、可再分发且样本量充足的数据，不能用本仓库的小型合成数据作出有效结论。

## 📁 目录

```
exchange/   虚拟交易所（A股+美股撮合，stdlib only）
dashboard/  演示面板服务 + 前端页面
tools/      决策记录与假设卡验收
demo/       合成演示数据生成器与数据
demo/tool-data/ 供工具读取的合成数据；用户运行输出默认写入 .paperlab-runtime/
```

接入你自己的数据：按 `demo/generate_demo_data.py` 中 `demo/tool-data/` 的 JSONL 格式准备你**有权使用**的数据；运行工具时通过 `PAPERLAB_DECISIONS_PATH`、`PAPERLAB_DATA_DIR` 或 `PAPERLAB_RUNTIME_DIR` 指向自己的数据或输出目录。默认示例只使用随库的合成数据。

## ⚠️ 声明

- 本项目**非官方**，与任何第三方交易平台**无关联、未获 endorsement**。
- 仅供研究学习，**不构成投资建议**；模拟结果不代表真实市场表现。
- 框架内置的纪律（参数冻结窗、假设卡、日亏熔断）是研究方法论的一部分，不保证盈利。

## 📄 许可证与商业使用

本项目采用 [PolyForm Noncommercial 1.0.0](LICENSE)：个人学习、研究、实验，以及符合许可定义的非营利组织可直接使用、修改和分发；**商业使用须另行取得书面许可**。因此它是 source-available，**不是 OSI 开源软件**。商业授权流程见 [COMMERCIAL-LICENSE.md](COMMERCIAL-LICENSE.md)。第三方说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
