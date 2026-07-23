# BTC 实时信号系统

面向 Polymarket 的 BTC 5 分钟 / 15 分钟实时信号分析系统。

首次运行先安装依赖：

```bash
python3 -m pip install -r requirements.txt
```

## 启动

推荐使用项目脚本后台启动：

```bash
./start.sh
```

重启与停止：

```bash
./restart.sh
./stop.sh
```

脚本会把进程号写入 `.runtime/btc-signal.pid`，运行日志写入 `.runtime/btc-signal.log`，并自动检测 `8000` 端口和 `/api/status` 健康状态。

环境变量会原样传递给服务，例如使用仿真模式重启：

```bash
BTC_SIGNAL_USE_SIMULATION=true ./restart.sh
```

也可以在前台直接运行：

```bash
python3 main.py
```

启动后访问 `http://127.0.0.1:8000`。

默认启用 Polymarket/Binance 实时数据采集。需要使用本地仿真数据时：

```bash
BTC_SIGNAL_USE_SIMULATION=true python3 main.py
```

实时模式会自动通过 Gamma API 搜索当前 BTC 涨跌市场并解析 UP/DOWN Token。CLOB WebSocket 持续接收完整订单簿、最佳买卖价和成交事件；Polymarket RTDS WebSocket 持续接收 Chainlink BTC/USD 实时价格。REST 仅用于市场发现、初始快照和断线降级，Binance 现货与永续合约价格作为参考价备用。

## 接口

- `GET /api/status`：当前服务状态和最新信号。
- `GET /api/markets`：最新的 5 分钟 / 15 分钟市场摘要。
- `GET /api/market/{market_id}`：单个市场的最新信号和本地滚动历史。
- `GET /api/stream`：SSE 实时数据流；连接后立即返回快照，CLOB 盘口事件最多每 50 毫秒合并推送一次，并每秒发送连接心跳。
- `POST /api/refresh`：立即触发一次数据刷新。

## 环境变量

- `BTC_SIGNAL_USE_SIMULATION=false`：启用 Polymarket/Binance 实时数据采集，默认值。
- `BTC_SIGNAL_USE_SIMULATION=true`：显式使用本地仿真行情。
- `BTC_SIGNAL_POLYMARKET_DISCOVERY=true`：启用 Gamma 市场自动发现，默认开启。
- `BTC_SIGNAL_POLYMARKET_GAMMA_API=https://gamma-api.polymarket.com`：设置 Gamma API 基础地址。
- `BTC_SIGNAL_POLYMARKET_CLOB_API=https://clob.polymarket.com`：设置 CLOB API 基础地址。
- `BTC_SIGNAL_HTTP_TIMEOUT=8`：设置 REST 请求超时时间，默认 8 秒，适配代理链路。
- `BTC_SIGNAL_MARKETS_FILE=examples/markets.example.json`：加载指定的市场定义文件。
- `BTC_SIGNAL_POLYMARKET_MARKET_URL`：使用自定义 JSON 市场接口覆盖 Gamma 接口。
- `BTC_SIGNAL_POLYMARKET_BOOK_URL`：使用自定义订单簿接口覆盖 CLOB 接口。
- `BTC_SIGNAL_POLYMARKET_TRADES_URL`：使用自定义最新成交接口覆盖 CLOB 接口。

接口模板可以使用 `{market_id}`、`{label}`、`{timeframe_minutes}` 和 `{symbol}` 变量。

## 价格字段

- `current_price`：优先使用 Polymarket RTDS 的 Chainlink BTC/USD 实时价格；断线时降级为 Binance 现货或永续合约价格。
- `target_price`：优先使用 Gamma 返回的 `priceToBeat`；短周期市场未返回该字段时，使用 RTDS 在对应窗口开始时的 Chainlink BTC/USD 价格。
- `price_gap`：`current_price - target_price`，单位与 BTC 报价币种一致。
- `contract_price`：Polymarket 看涨代币中间价，通常位于 `0..1`。
- `implied_probability`：当合约价格位于 `0..1` 时，计算为 `contract_price * 100`。
- `up_buy_price` / `up_sell_price`：UP 合约同一订单簿快照中的最低卖价 / 最高买价，对应买入成本 / 卖出所得。
- `down_buy_price` / `down_sell_price`：DOWN 合约同一订单簿快照中的最低卖价 / 最高买价，对应买入成本 / 卖出所得。

## 信号分析

- `market_probability`：UP 中间价与 `1 - DOWN 中间价` 的平均值，反映双边盘口给出的 UP 概率。
- `time_probability`：根据当前价、目标价、剩余结算秒数和最近 5 分钟实时波动率计算的目标价结算概率。
- `model_up_probability`：融合时间概率、双边盘口、BTC/合约动量、现货永续基差、盘口深度及最近 60 秒主动成交后的模型 UP 概率。
- `order_imbalance`：UP 与 DOWN 前五档盘口数量失衡合成值，范围 `-1..1`，正值偏 UP，负值偏 DOWN。
- `trade_imbalance`：UP 与 DOWN 最近 60 秒主动成交量合成值，范围 `-1..1`。
- `up_edge` / `down_edge`：模型概率减去对应可成交买入价和已知 taker fee 后的净优势，单位为百分点。
- `up_entry_price` / `down_entry_price`：在扣除已知 taker fee 后仍保留至少 2 个百分点模型净优势时，反推得到的最高可接受买入价，并向下对齐 Polymarket 对应合约的实时最小价格档位。该值是建议限价，不是当前卖一价，也不保证成交。
- `trade_action`：价值交易参考；净优势达到 2 个百分点且时间概率、盘口概率分歧不超过 25 个百分点时显示“买入 UP”或“买入 DOWN”，否则显示“暂不交易”。它与方向判断含义不同，可能在方向偏 UP 时因 DOWN 定价更便宜而给出买入 DOWN。

建议入场价仅在时间概率、双边盘口概率和数据完整度通过交易闸门时生成。即使当前盘口尚未达到建议价，页面仍会展示该价格作为等待挂单参考；UP 使用绿色，DOWN 使用红色。计算式为 `模型概率 - 入场价 - taker fee(入场价) >= 2%`。

信号方向在有目标价和结束时间时优先使用时间结算概率，缺失时才退回简单目标价距离。盘口概率与时间概率相差超过 25 个百分点时会提高观望权重。模型概率属于实时规则模型输出，尚未经过足量已结算市场的历史校准，不代表保证胜率。

页面会标记盘口报价来源。正常情况下显示 `CLOB WebSocket 实时盘口`，并根据 WebSocket 事件立即刷新。WebSocket 初始化或断线时使用同一次 CLOB `/book` 响应中的最佳买卖档，字段缺失时再使用 `/price` 补齐。

## 策略提醒

- 5 分钟和 15 分钟面板各自提供独立的“策略提醒”标签，默认关闭，开启状态会保存在当前浏览器中。
- 开启后，系统默认在对应市场结算倒计时进入最后 40 秒时检查实时模型的 `trade_action`。
- 当模型建议买入 UP、当前可成交买入价严格低于 `0.9` 且 BTC 价格差大于 `+15` 时触发 UP 提醒；DOWN 则要求价格差小于 `-15`。
- 5 分钟和 15 分钟策略分别维护提醒倒计时、最高买入价和最低价格差；开启对应面板的“策略提醒”后才显示设置按钮，参数通过不占用模块高度的浮动面板编辑，并分别保存在当前浏览器中。
- 同一个结算市场只提醒一次；UP 提醒窗使用绿色背景，DOWN 提醒窗使用红色背景。提醒是非模态 toast，不会抢占焦点或阻断页面点击，12 秒后自动消失。

## 时间与倒计时

- 所有可读时间统一使用上海时区，格式为 `yyyy-MM-DD hh:mm:ss`。
- 5 分钟和 15 分钟面板分别使用 Polymarket Gamma 市场结束时间计算结算倒计时，格式为 `mm:ss`。
- 系统通过 Polymarket CLOB `/time` 获取服务时间，每 30 秒重新校准本机时钟偏移，使倒计时与 Polymarket 页面一致。
- Gamma 未返回结束时间时，系统根据市场 slug 的 Unix 时间和市场周期推导；仿真模式按对应的 5 分钟 / 15 分钟时间边界生成。
- 页面倒计时和 Chainlink 当前价每秒刷新；UP/DOWN 盘口由 CLOB WebSocket 事件驱动刷新，不等待 REST 轮询周期。

macOS 开启系统代理时，Python 证书链可能不包含代理证书。系统会自动切换到系统 `curl` 并继续校验 HTTPS 证书，不会关闭 TLS 校验。

Gamma、CLOB 或 Binance 不可用时，服务会显示“部分实时”或“降级模式”。缺失的实时字段保持为 JSON `null`，系统不会生成虚假的当前价格或订单簿数据。

## 实时范围

当前版本通过 RTDS WebSocket 接收 Chainlink BTC/USD 秒级价格，通过 CLOB WebSocket 接收事件级实时盘口、深度变化和成交事件，通过 REST 刷新 Gamma 市场信息和 Binance 备用行情。5 分钟和 15 分钟市场的 UP/DOWN Token 分别使用独立 WebSocket，共四条单 Token 连接，避免高活跃盘口互相阻塞。CLOB 事件在后端最多合并 50 毫秒后推送；交易所时间戳落后超过 2 秒的积压事件会被直接丢弃，不允许覆盖当前盘口。实际端到端延迟可通过 `clob_event_latency_ms` 查看，丢弃计数可通过 `clob_realtime.dropped_stale_events` 查看。
