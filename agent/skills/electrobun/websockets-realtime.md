# WebSocket & Real-Time Data Patterns for Electrobun Trading Apps

## Architecture: WebSocket in Bun, Push to Webview via RPC

```
Broker WebSocket ──► Bun Process ──RPC messages──► Webview UI
                     (parsing,                     (rendering only)
                      filtering,
                      aggregation)
```

The Bun process manages ALL WebSocket connections. The webview NEVER connects directly to broker WebSockets. Bun processes raw tick data and pushes UI-ready updates via RPC messages.

## Market Data WebSocket Service

```typescript
// src/bun/services/market-data.ts
import type { BrokerConnection, TickData, TickMode } from "../broker/interface";

interface Subscription {
  symbol: string;
  mode: TickMode;
  callbacks: Set<(data: TickData) => void>;
  lastTick: TickData | null;
  throttleMs: number;
  lastEmit: number;
}

export class MarketDataService {
  private broker: BrokerConnection;
  private subscriptions = new Map<string, Subscription>();
  private connected = false;
  private reconnecting = false;

  constructor(broker: BrokerConnection) {
    this.broker = broker;

    // Handle ticks from broker
    broker.on("tick", (tick: TickData) => this.handleTick(tick));
    broker.on("disconnect", () => this.handleDisconnect());
    broker.on("reconnect", () => this.handleReconnect());
  }

  async connect(): Promise<void> {
    await this.broker.connectWebSocket();
    this.connected = true;
  }

  subscribe(
    symbol: string,
    mode: TickMode,
    callback: (data: TickData) => void,
    throttleMs = 100  // Throttle UI updates to max 10/sec per symbol
  ): () => void {
    let sub = this.subscriptions.get(symbol);

    if (!sub) {
      sub = {
        symbol,
        mode,
        callbacks: new Set(),
        lastTick: null,
        throttleMs,
        lastEmit: 0,
      };
      this.subscriptions.set(symbol, sub);
      // Subscribe at broker level
      this.broker.subscribe([symbol], mode);
    }

    sub.callbacks.add(callback);

    // Return unsubscribe function
    return () => {
      sub!.callbacks.delete(callback);
      if (sub!.callbacks.size === 0) {
        this.subscriptions.delete(symbol);
        this.broker.unsubscribe([symbol]);
      }
    };
  }

  getLastTick(symbol: string): TickData | null {
    return this.subscriptions.get(symbol)?.lastTick ?? null;
  }

  private handleTick(tick: TickData): void {
    const sub = this.subscriptions.get(tick.symbol);
    if (!sub) return;

    sub.lastTick = tick;

    // Throttle: only emit if enough time has passed
    const now = Date.now();
    if (now - sub.lastEmit < sub.throttleMs) return;
    sub.lastEmit = now;

    for (const cb of sub.callbacks) {
      try {
        cb(tick);
      } catch (e) {
        console.error(`Tick callback error for ${tick.symbol}:`, e);
      }
    }
  }

  private handleDisconnect(): void {
    this.connected = false;
    console.warn("Market data WebSocket disconnected");
  }

  private handleReconnect(): void {
    this.connected = true;
    this.reconnecting = false;
    console.log("Market data WebSocket reconnected");

    // Re-subscribe all active symbols
    const symbolsByMode = new Map<TickMode, string[]>();
    for (const [symbol, sub] of this.subscriptions) {
      if (!symbolsByMode.has(sub.mode)) symbolsByMode.set(sub.mode, []);
      symbolsByMode.get(sub.mode)!.push(symbol);
    }
    for (const [mode, symbols] of symbolsByMode) {
      this.broker.subscribe(symbols, mode);
    }
  }

  disconnect(): void {
    this.broker.disconnectWebSocket();
    this.connected = false;
    this.subscriptions.clear();
  }

  isConnected(): boolean {
    return this.connected;
  }
}
```

## Wiring Market Data to Webview

```typescript
// In src/bun/index.ts — connect market data to RPC messages
const marketData = new MarketDataService(broker);
await marketData.connect();

// Subscribe to watchlist symbols and push to all windows
const watchlistSymbols = ["NSE:NIFTY 50", "NSE:BANKNIFTY", "NSE:RELIANCE", "NSE:INFY"];

for (const symbol of watchlistSymbols) {
  marketData.subscribe(symbol, "quote", (tick) => {
    // Push to webview via RPC message
    mainWindow.webview.rpc?.send.tickUpdate({
      symbol: tick.symbol,
      ltp: tick.lastPrice,
      change: tick.change,
      volume: tick.volume,
    });
  }, 200); // Throttle: max 5 updates/sec per symbol for UI
}
```

## Webview Tick Rendering (Browser-Side)

```typescript
// src/mainview/index.ts — handle tick updates efficiently
const tickElements = new Map<string, {
  priceEl: HTMLElement;
  changeEl: HTMLElement;
  volumeEl: HTMLElement;
  lastPrice: number;
}>();

function initWatchlistRow(symbol: string): void {
  const row = document.createElement("tr");
  row.innerHTML = `
    <td class="symbol">${symbol}</td>
    <td class="price" id="price-${symbol}">--</td>
    <td class="change" id="change-${symbol}">--</td>
    <td class="volume" id="volume-${symbol}">--</td>
  `;
  document.getElementById("watchlist-body")!.appendChild(row);

  tickElements.set(symbol, {
    priceEl: document.getElementById(`price-${symbol}`)!,
    changeEl: document.getElementById(`change-${symbol}`)!,
    volumeEl: document.getElementById(`volume-${symbol}`)!,
    lastPrice: 0,
  });
}

function updateTickerUI(data: { symbol: string; ltp: number; change: number; volume: number }): void {
  const el = tickElements.get(data.symbol);
  if (!el) return;

  // Flash green/red on price change
  const direction = data.ltp > el.lastPrice ? "up" : data.ltp < el.lastPrice ? "down" : "same";
  el.lastPrice = data.ltp;

  el.priceEl.textContent = data.ltp.toFixed(2);
  el.priceEl.className = `price tick-${direction}`;

  el.changeEl.textContent = `${data.change >= 0 ? "+" : ""}${data.change.toFixed(2)}%`;
  el.changeEl.className = `change ${data.change >= 0 ? "positive" : "negative"}`;

  el.volumeEl.textContent = formatVolume(data.volume);

  // Remove flash class after animation
  requestAnimationFrame(() => {
    setTimeout(() => {
      el.priceEl.className = "price";
    }, 300);
  });
}

function formatVolume(vol: number): string {
  if (vol >= 10000000) return (vol / 10000000).toFixed(2) + " Cr";
  if (vol >= 100000) return (vol / 100000).toFixed(2) + " L";
  if (vol >= 1000) return (vol / 1000).toFixed(1) + " K";
  return String(vol);
}
```

## Local API Server for External Tools

Expose a local HTTP + WebSocket API so external tools (Python scripts, charting tools) can interact with the trading app.

```typescript
// src/bun/api/server.ts
import { BrowserWindow } from "electrobun/bun";

interface APIServerConfig {
  port: number;
  authToken: string;
  broker: BrokerConnection;
  orderManager: OrderManager;
}

export function startAPIServer(config: APIServerConfig) {
  const { port, authToken, broker, orderManager } = config;
  const wsClients = new Set<ServerWebSocket>();

  const server = Bun.serve({
    port,
    fetch(req, server) {
      // Authentication
      const token = req.headers.get("Authorization")?.replace("Bearer ", "");
      if (token !== authToken) {
        return Response.json({ error: "Unauthorized" }, { status: 401 });
      }

      // WebSocket upgrade
      if (req.headers.get("upgrade") === "websocket") {
        if (server.upgrade(req, { data: { authenticated: true } })) {
          return undefined;
        }
        return new Response("WebSocket upgrade failed", { status: 400 });
      }

      return handleHTTPRequest(req, broker, orderManager);
    },
    websocket: {
      open(ws) {
        wsClients.add(ws);
      },
      message(ws, message) {
        try {
          const msg = JSON.parse(String(message));
          handleWSMessage(ws, msg, broker);
        } catch (e) {
          ws.send(JSON.stringify({ error: "Invalid message" }));
        }
      },
      close(ws) {
        wsClients.delete(ws);
      },
    },
  });

  // Broadcast ticks to WebSocket clients
  broker.on("tick", (tick: TickData) => {
    const msg = JSON.stringify({ type: "tick", data: tick });
    for (const ws of wsClients) {
      ws.send(msg);
    }
  });

  // Broadcast order updates
  broker.on("order-update", (update) => {
    const msg = JSON.stringify({ type: "order_update", data: update });
    for (const ws of wsClients) {
      ws.send(msg);
    }
  });

  console.log(`API server running on http://localhost:${port}`);
  return server;
}

async function handleHTTPRequest(
  req: Request, broker: BrokerConnection, orderManager: OrderManager
): Promise<Response> {
  const url = new URL(req.url);

  try {
    switch (`${req.method} ${url.pathname}`) {
      case "GET /api/positions":
        return Response.json(await broker.getPositions());

      case "GET /api/orders":
        return Response.json(await broker.getOrders());

      case "GET /api/holdings":
        return Response.json(await broker.getHoldings());

      case "GET /api/margins":
        return Response.json(await broker.getMargins());

      case "POST /api/orders":
        const body = await req.json();
        return Response.json(await orderManager.place(body));

      case "DELETE /api/orders":
        const { orderId } = await req.json();
        return Response.json(await orderManager.cancel(orderId));

      case "POST /api/orders/cancel-all":
        return Response.json(await orderManager.cancelAll());

      case "POST /api/orders/square-off-all":
        return Response.json(await orderManager.squareOffAll());

      case "GET /api/ltp":
        const symbols = url.searchParams.getAll("symbol");
        return Response.json(await broker.getLTP(symbols));

      default:
        return Response.json({ error: "Not found" }, { status: 404 });
    }
  } catch (e) {
    return Response.json(
      { error: e instanceof Error ? e.message : "Internal error" },
      { status: 500 }
    );
  }
}

function handleWSMessage(ws: ServerWebSocket, msg: any, broker: BrokerConnection): void {
  switch (msg.action) {
    case "subscribe":
      broker.subscribe(msg.symbols, msg.mode || "quote");
      ws.send(JSON.stringify({ type: "subscribed", symbols: msg.symbols }));
      break;

    case "unsubscribe":
      broker.unsubscribe(msg.symbols);
      ws.send(JSON.stringify({ type: "unsubscribed", symbols: msg.symbols }));
      break;

    default:
      ws.send(JSON.stringify({ error: `Unknown action: ${msg.action}` }));
  }
}
```

## WebSocket Reconnection with State Reconciliation

```typescript
// After reconnecting, always reconcile state
async function reconcileAfterReconnect(
  broker: BrokerConnection,
  orderManager: OrderManager,
  mainWindow: BrowserWindow
): Promise<void> {
  console.log("Reconciling state after reconnect...");

  // 1. Refresh orders — some may have filled while disconnected
  const orders = await broker.getOrders();
  mainWindow.webview.rpc?.send.orderUpdate({
    orderId: "bulk-refresh",
    status: "REFRESHED",
    message: `Refreshed ${orders.length} orders after reconnect`,
  });

  // 2. Refresh positions — P&L may have changed
  const positions = await broker.getPositions();
  mainWindow.webview.rpc?.send.positionUpdate({ positions });

  // 3. Notify UI of connection restoration
  mainWindow.webview.rpc?.send.connectionStatus({
    broker: "zerodha",
    status: "connected",
  });
}
```

## Handling Binary Market Data Efficiently

```typescript
// Kite Connect sends binary tick data — parse without creating intermediate objects
function parseBinaryTicksFast(buffer: ArrayBuffer): void {
  const view = new DataView(buffer);
  const count = view.getInt16(0);
  let offset = 2;

  for (let i = 0; i < count; i++) {
    const len = view.getInt16(offset);
    offset += 2;

    const token = view.getInt32(offset);
    const ltp = view.getInt32(offset + 4) / 100;

    // Emit directly without creating TickData object for non-subscribed tokens
    const sub = subscriptionsByToken.get(token);
    if (sub) {
      // Only allocate TickData for subscribed symbols
      sub.lastPrice = ltp;
      if (len >= 32) {
        // Full mode — parse OHLCV
        sub.high = view.getInt32(offset + 8) / 100;
        sub.low = view.getInt32(offset + 12) / 100;
        sub.open = view.getInt32(offset + 16) / 100;
        sub.close = view.getInt32(offset + 20) / 100;
        sub.volume = view.getInt32(offset + 28);
      }
      sub.dirty = true;
    }

    offset += len;
  }
}
```

## Tick Aggregation for Candle Building

```typescript
// Build real-time candles from tick data
class CandleBuilder {
  private candles = new Map<string, Map<number, OHLC>>();
  private intervalMs: number;

  constructor(intervalMinutes: number) {
    this.intervalMs = intervalMinutes * 60 * 1000;
  }

  processTick(tick: TickData): OHLC | null {
    const candleTime = Math.floor(tick.timestamp / this.intervalMs) * this.intervalMs;
    let symbolCandles = this.candles.get(tick.symbol);

    if (!symbolCandles) {
      symbolCandles = new Map();
      this.candles.set(tick.symbol, symbolCandles);
    }

    let candle = symbolCandles.get(candleTime);

    if (!candle) {
      // New candle
      candle = {
        timestamp: new Date(candleTime).toISOString(),
        open: tick.lastPrice,
        high: tick.lastPrice,
        low: tick.lastPrice,
        close: tick.lastPrice,
        volume: tick.volume,
      };
      symbolCandles.set(candleTime, candle);

      // Clean old candles (keep last 500)
      if (symbolCandles.size > 500) {
        const oldest = Array.from(symbolCandles.keys()).sort()[0];
        symbolCandles.delete(oldest);
      }

      return candle;
    }

    // Update existing candle
    candle.high = Math.max(candle.high, tick.lastPrice);
    candle.low = Math.min(candle.low, tick.lastPrice);
    candle.close = tick.lastPrice;
    candle.volume = tick.volume;

    return candle;
  }
}
```

## Connection Health Monitoring

```typescript
// Monitor WebSocket health and alert on issues
class ConnectionMonitor {
  private lastTickTime = 0;
  private checkInterval: Timer | null = null;
  private staleThresholdMs = 10000; // 10 seconds without ticks = stale

  constructor(
    private marketData: MarketDataService,
    private onStatusChange: (status: "healthy" | "stale" | "disconnected") => void
  ) {}

  start(): void {
    this.checkInterval = setInterval(() => {
      if (!this.marketData.isConnected()) {
        this.onStatusChange("disconnected");
        return;
      }

      const now = Date.now();
      if (now - this.lastTickTime > this.staleThresholdMs) {
        this.onStatusChange("stale");
      } else {
        this.onStatusChange("healthy");
      }
    }, 2000);
  }

  recordTick(): void {
    this.lastTickTime = Date.now();
  }

  stop(): void {
    if (this.checkInterval) {
      clearInterval(this.checkInterval);
      this.checkInterval = null;
    }
  }
}
```
