const fmt = (value, digits = 2) => {
  if (value === null || value === undefined || Number.isNaN(value)) return "--";
  if (typeof value === "number") return value.toFixed(digits);
  return String(value);
};

const directionLabels = {
  long: "看涨",
  short: "看跌",
  neutral: "观望",
};

const tradeActionLabels = {
  buy_up: "买入 UP ↑",
  buy_down: "买入 DOWN ↓",
  hold: "暂不交易",
};

const modeLabels = {
  simulation: "仿真模式",
  live: "实时模式",
  "partial-live": "部分实时",
  fallback: "降级模式",
  initializing: "正在连接实时数据",
  error: "异常",
  unknown: "未知",
};

const fieldLabels = {
  type: "消息类型",
  updated_at: "更新时间",
  timestamp: "时间",
  poll_interval_seconds: "轮询间隔（秒）",
  use_simulation: "是否使用仿真数据",
  overall_source_mode: "总体数据模式",
  global_error: "全局错误",
  markets: "市场列表",
  market_id: "市场标识",
  label: "市场名称",
  timeframe_minutes: "周期（分钟）",
  latest: "最新信号",
  last_updated: "最后更新时间",
  source_mode: "数据源模式",
  last_error: "最后错误",
  direction: "信号方向",
  confidence: "置信度",
  long_probability: "看涨概率",
  short_probability: "看跌概率",
  neutral_probability: "观望概率",
  score: "综合评分",
  model_up_probability: "模型 UP 概率",
  market_probability: "双边盘口概率",
  time_probability: "时间结算概率",
  seconds_to_expiry: "剩余结算秒数",
  trade_action: "价值交易参考",
  up_edge: "UP 净优势",
  down_edge: "DOWN 净优势",
  up_entry_price: "UP 建议最高入场价",
  down_entry_price: "DOWN 建议最高入场价",
  data_quality: "数据完整度",
  reasons: "判断原因",
  snapshot: "行情快照",
  current_price: "BTC 当前价",
  target_price: "目标价",
  price_gap: "价格差",
  distance_to_target_pct: "距离目标价百分比",
  contract_price: "看涨合约价",
  implied_probability: "市场隐含概率",
  best_bid: "最佳买价",
  best_ask: "最佳卖价",
  up_buy_price: "UP 买入价",
  up_sell_price: "UP 卖出价",
  down_buy_price: "DOWN 买入价",
  down_sell_price: "DOWN 卖出价",
  last_trade: "最新成交价",
  spot_price: "现货价格",
  perp_price: "永续合约价格",
  volume_1m: "1 分钟成交量",
  volume_5m: "5 分钟成交量",
  volume_15m: "15 分钟成交量",
  order_imbalance: "订单簿失衡度",
  trade_imbalance: "60 秒成交失衡度",
  metadata: "元数据",
  source: "数据来源",
  market_bias: "市场方向偏好",
  polymarket_snapshot: "Polymarket 数据可用",
  binance_snapshot: "Binance 数据可用",
  market_start_time: "市场开始时间",
  market_end_time: "市场结束时间",
  market_end_timestamp: "市场结束时间戳",
  polymarket_clock_offset_seconds: "Polymarket 时钟偏移（秒）",
  up_token_id: "UP 代币标识",
  down_token_id: "DOWN 代币标识",
  quote_source: "盘口报价来源",
  realtime: "Chainlink 实时状态",
  clob_realtime: "CLOB 实时盘口状态",
  connected: "是否已连接",
  synced: "盘口是否已同步",
  connected_market_count: "已连接市场数",
  synced_market_count: "已同步市场数",
  market_count: "市场数",
  token_count: "Token 数",
  connection_count: "WebSocket 连接数",
  connections: "WebSocket 连接明细",
  last_message_timestamp: "最后消息时间戳",
  dropped_stale_events: "已丢弃过期事件数",
  up_bid_depth: "UP 前五档买盘深度",
  up_ask_depth: "UP 前五档卖盘深度",
  down_bid_depth: "DOWN 前五档买盘深度",
  down_ask_depth: "DOWN 前五档卖盘深度",
  up_order_imbalance: "UP 盘口深度失衡",
  down_order_imbalance: "DOWN 盘口深度失衡",
  up_trade_imbalance: "UP 成交失衡",
  down_trade_imbalance: "DOWN 成交失衡",
  point_count: "价格点数量",
  latest_timestamp: "最新价格时间戳",
  latest_price: "最新价格",
  clob_event_timestamp: "CLOB 事件时间戳",
  clob_event_latency_ms: "CLOB 事件延迟（毫秒）",
  clob_event_age_ms: "CLOB 事件年龄（毫秒）",
  clob_quote_complete: "CLOB 盘口是否完整",
};

const countdownTargets = { "5m": null, "15m": null };
const countdownClockOffsets = { "5m": 0, "15m": 0 };
const marketTargetPrices = { "5m": null, "15m": null };

const quoteSourceLabels = {
  clob_websocket: "CLOB WebSocket 实时盘口",
  clob_websocket_partial: "CLOB WebSocket 部分实时盘口",
  clob_book: "CLOB 同步订单簿",
  clob_book_with_price_fallback: "CLOB 订单簿 + /price 补齐",
  clob_price: "CLOB /price 可成交报价",
  clob_price_partial: "CLOB /price 部分报价",
  clob_price_with_book_fallback: "CLOB /price + 订单簿补齐",
  gamma_fallback: "Gamma 降级报价",
  simulation: "仿真盘口",
};

const formatTime = (value) => {
  if (!value) return "--";
  if (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(String(value))) return String(value);
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString("zh-CN", { hour12: false });
};

const formatQuote = (value) => {
  if (value === null || value === undefined || Number.isNaN(value)) return "--";
  return Number(value).toFixed(4);
};

const setBar = (id, value) => {
  const el = document.getElementById(id);
  if (!el) return;
  const width = Math.max(0, Math.min(100, Number(value) || 0));
  el.style.width = `${width}%`;
};

const updateCountdowns = () => {
  const localNow = Date.now() / 1000;
  Object.entries(countdownTargets).forEach(([suffix, target]) => {
    const el = document.getElementById(`countdown-${suffix}`);
    if (!el) return;
    if (!target) {
      el.textContent = "--:--";
      return;
    }
    const polymarketNow = localNow + (countdownClockOffsets[suffix] || 0);
    const remaining = Math.max(0, Math.floor(target - polymarketNow));
    const minutes = Math.floor(remaining / 60);
    const seconds = remaining % 60;
    el.textContent = `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  });
};

const formatPercent = (value) => {
  if (value === null || value === undefined || Number.isNaN(value)) return "--";
  return `${Number(value).toFixed(1)}%`;
};

const localizeValue = (value) => {
  if (Array.isArray(value)) return value.map(localizeValue);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).map(([key, nested]) => [fieldLabels[key] || key, localizeValue(nested)]),
    );
  }
  if (typeof value === "boolean") return value ? "是" : "否";
  if (typeof value === "string") {
    if (directionLabels[value]) return directionLabels[value];
    if (modeLabels[value]) return modeLabels[value];
    if (quoteSourceLabels[value]) return quoteSourceLabels[value];
    if (value === "snapshot") return "行情快照";
  }
  return value;
};

const setText = (id, value) => {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
};

const setPriceGap = (id, value) => {
  const el = document.getElementById(id);
  if (!el) return;
  const numericValue = value == null ? NaN : Number(value);
  el.textContent = fmt(Number.isFinite(numericValue) ? numericValue : null);
  el.classList.toggle("price-gap-positive", Number.isFinite(numericValue) && numericValue > 0);
  el.classList.toggle("price-gap-negative", Number.isFinite(numericValue) && numericValue < 0);
};

const setSignedPercent = (id, value) => {
  const el = document.getElementById(id);
  if (!el) return;
  const numericValue = value == null ? NaN : Number(value);
  el.textContent = Number.isFinite(numericValue)
    ? `${numericValue > 0 ? "+" : ""}${numericValue.toFixed(1)}%`
    : "--";
  el.classList.toggle("metric-positive", Number.isFinite(numericValue) && numericValue > 0);
  el.classList.toggle("metric-negative", Number.isFinite(numericValue) && numericValue < 0);
};

const renderMarket = (market) => {
  const suffix = market.timeframe_minutes <= 5 ? "5m" : "15m";
  const direction = market.direction || market.latest?.direction;
  const snapshot = market.snapshot || market.latest?.snapshot || {};
  const directionElement = document.getElementById(`direction-${suffix}`);
  setText(`updated-${suffix}`, formatTime(market.timestamp || market.last_updated));
  setText(`direction-${suffix}`, directionLabels[direction] || "--");
  if (directionElement) directionElement.dataset.direction = direction || "";
  const confidence = market.confidence ?? market.latest?.confidence;
  setText(`confidence-${suffix}`, formatPercent(confidence === undefined ? undefined : confidence * 100));
  const longProbability = market.long_probability ?? market.latest?.long_probability;
  const shortProbability = market.short_probability ?? market.latest?.short_probability;
  const neutralProbability = market.neutral_probability ?? market.latest?.neutral_probability;
  setText(`long-${suffix}`, formatPercent(longProbability));
  setText(`short-${suffix}`, formatPercent(shortProbability));
  setText(`neutral-${suffix}`, formatPercent(neutralProbability));
  setBar(`long-bar-${suffix}`, longProbability);
  setBar(`short-bar-${suffix}`, shortProbability);
  setBar(`neutral-bar-${suffix}`, neutralProbability);
  setText(`score-${suffix}`, fmt(market.score ?? market.latest?.score, 2));
  const tradeAction = market.trade_action ?? market.latest?.trade_action;
  const tradeActionElement = document.getElementById(`trade-action-${suffix}`);
  setText(`trade-action-${suffix}`, tradeActionLabels[tradeAction] || "--");
  if (tradeActionElement) tradeActionElement.dataset.action = tradeAction || "";
  setText(`model-up-${suffix}`, formatPercent(market.model_up_probability ?? market.latest?.model_up_probability));
  setText(`time-probability-${suffix}`, formatPercent(market.time_probability ?? market.latest?.time_probability));
  setText(`market-probability-${suffix}`, formatPercent(market.market_probability ?? market.latest?.market_probability));
  const dataQuality = market.data_quality ?? market.latest?.data_quality;
  setText(`data-quality-${suffix}`, formatPercent(dataQuality == null ? null : dataQuality * 100));
  setSignedPercent(`up-edge-${suffix}`, market.up_edge ?? market.latest?.up_edge);
  setSignedPercent(`down-edge-${suffix}`, market.down_edge ?? market.latest?.down_edge);
  setText(`up-entry-${suffix}`, formatQuote(market.up_entry_price ?? market.latest?.up_entry_price));
  setText(`down-entry-${suffix}`, formatQuote(market.down_entry_price ?? market.latest?.down_entry_price));
  setSignedPercent(`order-imbalance-${suffix}`, snapshot.order_imbalance == null ? null : snapshot.order_imbalance * 100);
  setSignedPercent(`trade-imbalance-${suffix}`, snapshot.trade_imbalance == null ? null : snapshot.trade_imbalance * 100);
  setText(`current-${suffix}`, fmt(snapshot.current_price));
  setText(`target-${suffix}`, fmt(snapshot.target_price));
  setPriceGap(`gap-${suffix}`, snapshot.price_gap);
  const targetValue = snapshot.target_price;
  marketTargetPrices[suffix] = targetValue == null || !Number.isFinite(Number(targetValue))
    ? null
    : Number(targetValue);
  setText(`contract-${suffix}`, formatQuote(snapshot.contract_price));

  const quoteFields = [
    ["up-buy", snapshot.up_buy_price],
    ["up-sell", snapshot.up_sell_price],
    ["down-buy", snapshot.down_buy_price],
    ["down-sell", snapshot.down_sell_price],
  ];
  quoteFields.forEach(([name, value]) => setText(`${name}-${suffix}`, formatQuote(value)));
  const upSpread = snapshot.up_buy_price != null && snapshot.up_sell_price != null
    ? snapshot.up_buy_price - snapshot.up_sell_price
    : null;
  const downSpread = snapshot.down_buy_price != null && snapshot.down_sell_price != null
    ? snapshot.down_buy_price - snapshot.down_sell_price
    : null;
  setText(`up-spread-${suffix}`, formatQuote(upSpread));
  setText(`down-spread-${suffix}`, formatQuote(downSpread));
  const quoteSource = snapshot.metadata?.quote_source || snapshot.metadata?.source;
  setText(`quote-source-${suffix}`, quoteSourceLabels[quoteSource] || "报价暂不可用");

  let marketEnd = Number(snapshot.metadata?.market_end_timestamp);
  if ((!Number.isFinite(marketEnd) || marketEnd <= 0) && snapshot.metadata?.source === "simulation") {
    const timeframeSeconds = market.timeframe_minutes * 60;
    const now = Date.now() / 1000;
    marketEnd = (Math.floor(now / timeframeSeconds) + 1) * timeframeSeconds;
  }
  countdownTargets[suffix] = Number.isFinite(marketEnd) && marketEnd > 0 ? marketEnd : null;
  const clockOffset = Number(snapshot.metadata?.polymarket_clock_offset_seconds);
  countdownClockOffsets[suffix] = Number.isFinite(clockOffset) ? clockOffset : 0;
  updateCountdowns();

  const reasons = market.reasons ?? market.latest?.reasons ?? [];
  const container = document.getElementById(`reasons-${suffix}`);
  if (container) {
    container.innerHTML = "";
    reasons.forEach((reason) => {
      const span = document.createElement("span");
      span.textContent = reason;
      container.appendChild(span);
    });
  }
};

const render = (payload) => {
  const markets = payload.markets || payload?.markets || [];
  const feed = document.getElementById("feed-json");
  if (feed) feed.textContent = JSON.stringify(localizeValue(payload), null, 2);
  const updatedAt = payload.updated_at || payload.timestamp;
  setText("updated-feed", formatTime(updatedAt));
  setText("status-line", payload.global_error ? `数据源异常：${payload.global_error}` : `更新时间：${formatTime(updatedAt)}`);
  const mode = payload.overall_source_mode || (payload.use_simulation === false ? "live" : "simulation");
  setText("mode-pill", modeLabels[mode] || "未知模式");
  markets.forEach(renderMarket);
};

const connectStream = () => {
  const source = new EventSource("/api/stream");
  source.onmessage = (event) => {
    try {
      render(JSON.parse(event.data));
    } catch (error) {
      console.error(error);
    }
  };
  source.addEventListener("snapshot", (event) => {
    try {
      render(JSON.parse(event.data));
    } catch (error) {
      console.error(error);
    }
  });
  source.addEventListener("heartbeat", (event) => {
    try {
      const heartbeat = JSON.parse(event.data);
      const price = Number(heartbeat.realtime?.latest_price);
      if (!heartbeat.realtime?.connected || !Number.isFinite(price)) return;
      ["5m", "15m"].forEach((suffix) => {
        setText(`current-${suffix}`, fmt(price));
        const target = marketTargetPrices[suffix];
        setPriceGap(`gap-${suffix}`, target == null ? null : price - target);
      });
      setText("updated-feed", formatTime(heartbeat.timestamp));
    } catch (error) {
      console.error(error);
    }
  });
  source.onerror = () => {
    setText("status-line", "实时数据流正在重新连接...");
  };
};

document.getElementById("refresh-btn")?.addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    const response = await fetch("/api/refresh", { method: "POST" });
    render(await response.json());
  } finally {
    button.disabled = false;
  }
});

fetch("/api/status")
  .then((response) => response.json())
  .then(render)
  .catch((error) => console.error(error));

connectStream();
updateCountdowns();
setInterval(updateCountdowns, 1000);
