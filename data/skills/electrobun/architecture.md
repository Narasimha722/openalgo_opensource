# Electrobun Algo Trading Architecture

## Process Model

Electrobun uses a multi-process architecture:

```
┌─────────────────────────────────────────────────┐
│                  Launcher (Zig)                  │
│  Tiny binary → spawns Bun → inits native GUI    │
└──────────────────────┬──────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────┐
│              Bun Main Process                    │
│  ┌─────────────┐  ┌──────────────┐             │
│  │ Broker APIs  │  │ SQLite DB    │             │
│  │ WebSockets   │  │ Order Mgmt   │             │
│  │ Risk Engine  │  │ Strategy     │             │
│  └──────┬──────┘  └──────┬───────┘             │
│         │    Encrypted RPC    │                  │
│         │   (AES-256-GCM)    │                  │
│  ┌──────▼────────────────▼───────┐              │
│  │     RPC Transport Layer       │              │
│  │  WebSocket localhost + FFI    │              │
│  └──────────────┬────────────────┘              │
└─────────────────┼───────────────────────────────┘
                  │
┌─────────────────▼───────────────────────────────┐
│           Native Webview (per-window)            │
│  WKWebView (mac) / WebView2 (win) / GTK (linux) │
│  ┌─────────────────────────────────────┐        │
│  │  Electroview + UI (HTML/CSS/TS)     │        │
│  │  Dashboard, Charts, Order Entry     │        │
│  │  NO direct broker/DB access         │        │
│  └─────────────────────────────────────┘        │
└─────────────────────────────────────────────────┘
```

## Layer Responsibilities

### Bun Layer (src/bun/) — The Trading Engine

This is where ALL business logic lives:
- Broker API authentication and session management
- WebSocket connections to market data feeds
- Order placement, modification, cancellation
- Position tracking and P&L calculation
- Risk management (pre-trade checks, circuit breakers)
- Strategy execution engine
- SQLite database operations (trades, logs, config)
- Encryption and key management
- Local HTTP/WebSocket server for external tool integration

### Webview Layer (src/mainview/) — The Display

Pure presentation:
- Renders data received via RPC messages
- Sends user actions (place order, start strategy) via RPC requests
- Chart rendering, table displays, forms
- NEVER holds API keys, tokens, or database handles
- NEVER makes direct HTTP calls to brokers

### Shared Types (src/shared/) — The Contract

- RPC schema type definitions
- Data model interfaces (Order, Position, OHLC, etc.)
- Enum definitions (OrderType, OrderStatus, Exchange)

## Multi-Window Architecture

Trading apps often need multiple windows:

```typescript
import { BrowserWindow, BrowserView } from "electrobun/bun";

// Main trading window
const mainWindow = new BrowserWindow({
  title: "Trading Dashboard",
  url: "views://mainview/index.html",
  frame: { x: 0, y: 0, width: 1400, height: 900 },
  rpc: mainRpc,
});

// Separate order book window
const orderbookWindow = new BrowserWindow({
  title: "Order Book",
  url: "views://orderbook/index.html",
  frame: { x: 1400, y: 0, width: 500, height: 900 },
  rpc: orderbookRpc,
});

// Chart window (can load external charting lib in sandboxed webview)
const chartWindow = new BrowserWindow({
  title: "Charts",
  url: "views://charts/index.html",
  frame: { x: 0, y: 900, width: 1900, height: 600 },
  rpc: chartRpc,
});

// Broadcast tick data to all windows
function broadcastTick(data: TickData) {
  mainWindow.webview.rpc?.send.tickUpdate(data);
  orderbookWindow.webview.rpc?.send.tickUpdate(data);
  chartWindow.webview.rpc?.send.tickUpdate(data);
}
```

## Embedded Webviews for Third-Party Content

Use `<electrobun-webview>` for isolated content like broker login pages:

```html
<!-- In your main HTML -->
<electrobun-webview
  id="broker-login"
  src="https://kite.zerodha.com/connect/login"
  sandbox
  style="width: 100%; height: 500px;">
</electrobun-webview>
```

The `sandbox` attribute prevents RPC injection, keeping the broker's login page isolated.

## Service Architecture Pattern

```typescript
// src/bun/services/market-data.ts
export class MarketDataService {
  private ws: WebSocket | null = null;
  private subscriptions = new Map<string, Set<(data: TickData) => void>>();
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 10;
  private reconnectDelay = 1000;

  constructor(private broker: BrokerConnection) {}

  async connect() {
    this.ws = new WebSocket(this.broker.getWebSocketUrl());
    this.ws.onmessage = (event) => this.handleMessage(event);
    this.ws.onclose = () => this.handleDisconnect();
    this.ws.onerror = (err) => this.handleError(err);
    this.reconnectAttempts = 0;
  }

  subscribe(symbol: string, callback: (data: TickData) => void) {
    if (!this.subscriptions.has(symbol)) {
      this.subscriptions.set(symbol, new Set());
      this.ws?.send(JSON.stringify({ action: "subscribe", symbol }));
    }
    this.subscriptions.get(symbol)!.add(callback);
  }

  private handleMessage(event: MessageEvent) {
    const data = this.parseTickData(event.data);
    const callbacks = this.subscriptions.get(data.symbol);
    if (callbacks) {
      for (const cb of callbacks) cb(data);
    }
  }

  private async handleDisconnect() {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      console.error("Max reconnection attempts reached");
      return;
    }
    const delay = this.reconnectDelay * Math.pow(2, this.reconnectAttempts);
    this.reconnectAttempts++;
    await Bun.sleep(delay);
    await this.connect();
    // Re-subscribe to all symbols after reconnect
    for (const symbol of this.subscriptions.keys()) {
      this.ws?.send(JSON.stringify({ action: "subscribe", symbol }));
    }
  }
}
```

## Application Menu for Trading

```typescript
ApplicationMenu.setApplicationMenu([
  {
    label: "Trading App",
    submenu: [
      { role: "about" },
      { type: "separator" },
      { label: "Preferences", action: "preferences", accelerator: "," },
      { type: "separator" },
      { role: "hide" },
      { role: "hideOthers" },
      { role: "quit" },
    ],
  },
  {
    label: "Trading",
    submenu: [
      { label: "New Order", action: "new-order", accelerator: "n" },
      { label: "Cancel All Orders", action: "cancel-all", accelerator: "Shift+x" },
      { type: "separator" },
      { label: "Start Strategy", action: "start-strategy" },
      { label: "Stop All Strategies", action: "stop-all-strategies" },
      { type: "separator" },
      { label: "Square Off All", action: "square-off-all", accelerator: "Shift+q" },
    ],
  },
  {
    label: "View",
    submenu: [
      { label: "Dashboard", action: "view-dashboard", accelerator: "1" },
      { label: "Order Book", action: "view-orderbook", accelerator: "2" },
      { label: "Positions", action: "view-positions", accelerator: "3" },
      { label: "Charts", action: "view-charts", accelerator: "4" },
      { type: "separator" },
      { role: "toggleFullScreen" },
    ],
  },
  {
    label: "Edit",
    submenu: [
      { role: "undo" }, { role: "redo" },
      { type: "separator" },
      { role: "cut" }, { role: "copy" }, { role: "paste" }, { role: "selectAll" },
    ],
  },
]);
```

## Tray for Background Trading

```typescript
const tray = new Tray({
  title: "Trading",
  template: true, // Adapts to macOS light/dark mode
});

function updateTrayMenu(state: AppState) {
  tray.setMenu([
    { label: `P&L: ${state.totalPnL >= 0 ? "+" : ""}${state.totalPnL.toFixed(2)}`, enabled: false },
    { label: `Open Positions: ${state.openPositions}`, enabled: false },
    { label: `Pending Orders: ${state.pendingOrders}`, enabled: false },
    { type: "separator" },
    { label: "Show Dashboard", action: "show-dashboard" },
    { label: "Quick Order", action: "quick-order" },
    { type: "separator" },
    {
      label: "Emergency",
      submenu: [
        { label: "Cancel All Orders", action: "cancel-all" },
        { label: "Square Off All", action: "square-off" },
      ],
    },
    { type: "separator" },
    { label: "Quit", action: "quit" },
  ]);
}
```

## Error Handling Strategy

```typescript
// Wrap all RPC handlers with consistent error handling
function wrapHandler<P, R>(
  name: string,
  handler: (params: P) => Promise<R>
): (params: P) => Promise<R> {
  return async (params: P) => {
    try {
      return await handler(params);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      console.error(`[RPC:${name}] Error:`, message);
      // Log to DB for audit
      db.run(
        "INSERT INTO error_log (handler, message, params, timestamp) VALUES (?, ?, ?, ?)",
        name, message, JSON.stringify(params), Date.now()
      );
      throw error; // Re-throw so webview gets the error
    }
  };
}
```

## Updater Integration

```typescript
// Check for updates on startup (non-blocking)
async function checkUpdates() {
  try {
    const info = await Updater.checkForUpdate();
    if (info.updateAvailable) {
      mainWindow.webview.rpc?.send.connectionStatus({
        broker: "app",
        status: "update-available",
      });
      // Download in background
      await Updater.downloadUpdate();
      // Notify user, don't force-apply during trading hours
      Utils.showNotification({
        title: "Update Available",
        body: `Version ${info.version} is ready to install.`,
      });
    }
  } catch (e) {
    console.error("Update check failed:", e);
  }
}

// Only apply updates when user explicitly requests (not during trading)
async function applyUpdate() {
  const positions = await broker.getPositions();
  const openPositions = positions.filter(p => p.quantity !== 0);
  if (openPositions.length > 0) {
    Utils.showNotification({
      title: "Cannot Update",
      body: "Close all positions before updating.",
    });
    return;
  }
  await Updater.applyUpdate();
}
```
