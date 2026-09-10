# OpenAlgo → Electrobun Migration Guide

## Architecture Mapping

OpenAlgo is a Python (Flask) + React web app. Converting to Electrobun means:

| OpenAlgo (Web) | Electrobun (Desktop) |
|---|---|
| Flask backend (Python) | Bun main process (TypeScript) |
| React SPA (Vite) | Electrobun views (HTML/CSS/TS) |
| Flask-SocketIO | RPC messages (bun → webview) |
| WebSocket proxy server (port 8765) | Bun WebSocket client (direct to broker) |
| REST API (`/api/v1/*`) | RPC request handlers + optional local HTTP server |
| ZeroMQ message bus | In-process event emitter (single Bun process) |
| SQLAlchemy ORM + SQLite/PostgreSQL | `bun:sqlite` with prepared statements |
| DuckDB (historify) | `bun:sqlite` or Bun FFI to DuckDB |
| Flask sessions + cookies | In-memory state (desktop app, single user) |
| CSRF tokens | Not needed (no web-accessible endpoints) |
| Gunicorn + eventlet | Not needed (Bun handles concurrency) |
| React Router (74 pages) | Multi-view RPC or single-view with client-side routing |
| Zustand stores | Webview-side state (vanilla TS or lightweight store) |
| Broker Python HTTP (httpx) | Bun `fetch()` with same HTTP APIs |
| subprocess.Popen (strategies) | `Bun.spawn()` for strategy execution |
| APScheduler | `setInterval` / `setTimeout` or Bun cron patterns |
| Telegram bot (python-telegram-bot) | Bun `fetch()` to Telegram Bot API |
| ngrok tunnels | Not needed (desktop app, direct broker WebSocket) |

## What Gets Simpler

1. **No web server needed** — Desktop app, no HTTP endpoints to protect
2. **No CSRF/CORS** — No browser security model concerns
3. **No session management** — Single user, in-memory state
4. **No ZeroMQ** — Single Bun process handles everything
5. **No WebSocket proxy** — Bun connects directly to broker WebSocket
6. **No rate limiting on API** — Local app, no external access (unless you expose a local API)
7. **No deployment infrastructure** — Ship as a `.app`/`.exe` with Electrobun Updater

## What Gets Different

1. **Broker auth tokens** — Store encrypted in SQLite (Bun-side), not Flask sessions
2. **Market data flow** — Broker WebSocket → Bun process → RPC messages to webview
3. **Strategy execution** — `Bun.spawn()` instead of `subprocess.Popen()`, same isolation model
4. **Multi-broker** — Plugin modules in TypeScript instead of Python
5. **Paper trading** — Same logic, different language (TS instead of Python)

## Project Structure Mapping

```
openalgo/                          →  trading-app/
├── app.py                         →  src/bun/index.ts (app entry)
├── broker/                        →  src/bun/broker/ (plugin system)
│   ├── zerodha/                   →  src/bun/broker/zerodha/
│   │   ├── api/order_api.py       →  src/bun/broker/zerodha/orders.ts
│   │   ├── api/auth_api.py        →  src/bun/broker/zerodha/auth.ts
│   │   ├── api/data.py            →  src/bun/broker/zerodha/data.ts
│   │   ├── api/funds.py           →  src/bun/broker/zerodha/funds.ts
│   │   ├── mapping/transform.py   →  src/bun/broker/zerodha/mapping.ts
│   │   └── streaming/adapter.py   →  src/bun/broker/zerodha/websocket.ts
│   └── {28 more brokers}          →  src/bun/broker/{brokers}/
├── blueprints/                    →  (merged into RPC handlers)
│   ├── auth.py                    →  src/bun/services/auth.ts
│   ├── python_strategy.py         →  src/bun/services/strategy-runner.ts
│   └── flow.py                    →  src/bun/services/flow-engine.ts
├── database/                      →  src/bun/db/
│   ├── auth_db.py                 →  src/bun/db/auth.ts
│   ├── user_db.py                 →  src/bun/db/users.ts
│   └── apilog_db.py               →  src/bun/db/logs.ts
├── services/                      →  src/bun/services/
│   ├── place_order_service.py     →  src/bun/services/order-service.ts
│   ├── quotes_service.py          →  src/bun/services/market-data.ts
│   └── ...50+ services            →  src/bun/services/...
├── sandbox/                       →  src/bun/sandbox/
│   ├── execution_engine.py        →  src/bun/sandbox/engine.ts
│   ├── fund_manager.py            →  src/bun/sandbox/funds.ts
│   └── position_manager.py        →  src/bun/sandbox/positions.ts
├── websocket_proxy/               →  (eliminated: Bun connects directly)
├── restx_api/                     →  src/bun/api/ (local HTTP server, optional)
├── frontend/src/                  →  src/mainview/ (Electrobun views)
│   ├── pages/                     →  (multi-view or SPA within single view)
│   ├── components/                →  src/mainview/components/
│   ├── hooks/                     →  src/mainview/hooks/
│   ├── stores/                    →  src/mainview/stores/
│   └── api/                       →  (replaced by RPC calls)
└── .env                           →  src/bun/config.ts (encrypted in SQLite)
```

## RPC Schema for Full OpenAlgo Feature Set

```typescript
import type { RPCSchema } from "electrobun/bun";

type OpenAlgoRPC = {
  bun: RPCSchema<{
    requests: {
      // --- Authentication ---
      login: { params: { username: string; password: string }; response: { success: boolean; error?: string } };
      logout: { params: {}; response: { success: boolean } };
      getSessionState: { params: {}; response: { user: string | null; broker: string | null; apiKey: string | null } };
      brokerLogin: { params: { broker: string; apiKey: string; apiSecret: string; totp?: string }; response: { success: boolean; redirectUrl?: string } };
      handleBrokerCallback: { params: { broker: string; requestToken: string }; response: { success: boolean } };
      generateApiKey: { params: {}; response: { apiKey: string } };

      // --- Orders ---
      placeOrder: { params: OrderParams; response: OrderResult };
      placeSmartOrder: { params: SmartOrderParams; response: OrderResult };
      modifyOrder: { params: ModifyParams; response: { success: boolean } };
      cancelOrder: { params: { orderId: string }; response: { success: boolean } };
      cancelAllOrders: { params: {}; response: { cancelled: number; failed: number } };
      closePosition: { params: ClosePositionParams; response: { success: boolean } };
      closeAllPositions: { params: {}; response: { closed: number; failed: number } };
      basketOrder: { params: { orders: OrderParams[] }; response: OrderResult[] };
      splitOrder: { params: SplitOrderParams; response: OrderResult[] };

      // --- Market Data ---
      getQuote: { params: { symbol: string; exchange: string }; response: QuoteData };
      getMultiQuotes: { params: { symbols: { symbol: string; exchange: string }[] }; response: Record<string, QuoteData> };
      getDepth: { params: { symbol: string; exchange: string }; response: DepthData };
      getHistory: { params: HistoryParams; response: OHLC[] };
      subscribeMarketData: { params: { symbols: string[]; mode: "ltp" | "quote" | "depth" }; response: { success: boolean } };
      unsubscribeMarketData: { params: { symbols: string[] }; response: { success: boolean } };

      // --- Options ---
      getOptionChain: { params: { symbol: string; expiry: string }; response: OptionChainData };
      getOptionGreeks: { params: OptionGreeksParams; response: GreeksData };
      getExpiries: { params: { symbol: string; exchange: string }; response: string[] };

      // --- Portfolio ---
      getPositions: { params: {}; response: Position[] };
      getHoldings: { params: {}; response: Holding[] };
      getOrders: { params: {}; response: Order[] };
      getTrades: { params: {}; response: Trade[] };
      getFunds: { params: {}; response: FundsData };
      getOrderHistory: { params: { orderId: string }; response: OrderUpdate[] };

      // --- Instruments ---
      searchInstruments: { params: { query: string }; response: Instrument[] };
      downloadMasterContract: { params: { exchange: string }; response: { success: boolean; count: number } };

      // --- Strategies ---
      getStrategies: { params: {}; response: Strategy[] };
      createStrategy: { params: StrategyConfig; response: { id: string } };
      deleteStrategy: { params: { id: string }; response: { success: boolean } };
      startStrategy: { params: { id: string }; response: { success: boolean } };
      stopStrategy: { params: { id: string }; response: { success: boolean } };
      getStrategyLogs: { params: { id: string; lines?: number }; response: string[] };

      // --- Python Strategies ---
      getPythonStrategies: { params: {}; response: PythonStrategy[] };
      createPythonStrategy: { params: { name: string; code: string }; response: { id: string } };
      updatePythonStrategy: { params: { id: string; code: string }; response: { success: boolean } };
      runPythonStrategy: { params: { id: string }; response: { pid: number } };
      stopPythonStrategy: { params: { id: string }; response: { success: boolean } };
      schedulePythonStrategy: { params: { id: string; schedule: ScheduleConfig }; response: { success: boolean } };

      // --- Flow Workflows ---
      getFlows: { params: {}; response: Flow[] };
      getFlow: { params: { id: string }; response: Flow };
      saveFlow: { params: { id: string; nodes: FlowNode[]; edges: FlowEdge[] }; response: { success: boolean } };
      executeFlow: { params: { id: string }; response: { success: boolean } };
      deleteFlow: { params: { id: string }; response: { success: boolean } };

      // --- Sandbox ---
      toggleSandbox: { params: { enabled: boolean }; response: { success: boolean } };
      getSandboxPositions: { params: {}; response: SandboxPosition[] };
      getSandboxOrders: { params: {}; response: SandboxOrder[] };
      getSandboxPnL: { params: {}; response: SandboxPnLData };
      resetSandbox: { params: {}; response: { success: boolean } };

      // --- Telegram ---
      configureTelegram: { params: { botToken: string; chatId: string }; response: { success: boolean } };
      testTelegram: { params: {}; response: { success: boolean } };

      // --- Settings ---
      getSettings: { params: {}; response: AppSettings };
      updateSettings: { params: Partial<AppSettings>; response: { success: boolean } };
      setOrderMode: { params: { mode: "auto" | "semi_auto" }; response: { success: boolean } };

      // --- Action Center (semi-auto) ---
      getPendingOrders: { params: {}; response: PendingOrder[] };
      approveOrder: { params: { id: string }; response: OrderResult };
      rejectOrder: { params: { id: string }; response: { success: boolean } };

      // --- Monitoring ---
      getHealthStatus: { params: {}; response: HealthData };
      getTrafficLogs: { params: { page: number; limit: number }; response: TrafficLog[] };
      getLatencyStats: { params: {}; response: LatencyStats };

      // --- Market Calendar ---
      getMarketHolidays: { params: { year?: number }; response: Holiday[] };
      getMarketTimings: { params: {}; response: MarketTiming[] };
      isMarketOpen: { params: {}; response: { open: boolean; nextOpen?: string } };

      // --- Historify ---
      getHistorifyConfig: { params: {}; response: HistorifyConfig };
      startHistorify: { params: HistorifyConfig; response: { success: boolean } };
      getHistorifyData: { params: { symbol: string; interval: string; from: string; to: string }; response: OHLC[] };

      // --- Admin ---
      getUsers: { params: {}; response: User[] };
      getFreezeQuantities: { params: {}; response: FreezeQty[] };
      updateFreezeQuantity: { params: FreezeQty; response: { success: boolean } };

      // --- Export ---
      exportTrades: { params: { from: string; to: string; format: "csv" | "json" }; response: { filePath: string } };
      exportPnL: { params: { from: string; to: string }; response: { filePath: string } };
    };
    messages: {
      logMessage: { level: string; message: string; context?: string };
    };
  }>;
  webview: RPCSchema<{
    requests: {};
    messages: {
      // Real-time market data
      tickUpdate: TickData;
      depthUpdate: { symbol: string; exchange: string; depth: DepthData };

      // Order/trade events
      orderEvent: { type: "placed" | "modified" | "cancelled" | "filled" | "rejected"; data: Order };
      tradeEvent: Trade;
      positionUpdate: Position[];

      // Strategy events
      strategyLog: { strategyId: string; message: string; level: string; timestamp: number };
      strategyStatus: { strategyId: string; status: "running" | "stopped" | "error"; pid?: number };

      // Flow events
      flowExecutionLog: { flowId: string; nodeId: string; status: string; output: any };

      // Sandbox events
      sandboxOrderEvent: { type: string; data: SandboxOrder };
      sandboxPositionUpdate: SandboxPosition[];

      // System events
      connectionStatus: { service: string; status: "connected" | "disconnected" | "reconnecting" };
      masterContractProgress: { exchange: string; progress: number; total: number };
      notification: { title: string; body: string; type: "info" | "success" | "warning" | "error"; category: string };

      // Action center (semi-auto)
      pendingOrderCreated: PendingOrder;

      // Telegram
      telegramMessage: { chatId: string; message: string };
    };
  }>;
};
```

## Migration Priority Order

### Phase 1: Core Trading (MVP)
1. User auth (local, single user — simplified from web version)
2. Broker plugin system (start with 2-3 brokers)
3. Order placement, modification, cancellation
4. Positions, holdings, order book, trade book
5. Market data via broker WebSocket → RPC messages to UI
6. Basic dashboard, order entry, position views

### Phase 2: Data & Analytics
7. SQLite database (orders, trades, audit log)
8. Historical data fetching and caching
9. Option chain with live data
10. Options analytics (Greeks, IV, OI)
11. Funds and margin display

### Phase 3: Automation
12. Webhook strategy system
13. Python strategy execution via `Bun.spawn()`
14. Strategy scheduling
15. Flow workflow engine (visual node editor)

### Phase 4: Advanced Features
16. Sandbox/paper trading mode
17. Action center (semi-auto order approval)
18. Telegram bot integration
19. Local HTTP API server for external tools
20. Market calendar (holidays, timings)

### Phase 5: Polish & Distribution
21. System tray with P&L summary
22. Health monitoring
23. Auto-updater via Electrobun Updater
24. Code signing and notarization
25. Multi-platform builds (macOS, Windows, Linux)

## Key Decisions for Electrobun Conversion

### UI Approach: Multi-View vs Single View SPA

**Option A: Single main view with client-side routing** (recommended)
- One `BrowserWindow` with one view
- Use a lightweight client-side router in the webview
- Simpler, matches OpenAlgo's existing React SPA model
- Fewer RPC schemas to manage

**Option B: Multi-window with separate views**
- Separate windows for dashboard, order book, charts
- Better for multi-monitor trading setups
- More complex RPC wiring (broadcast to all windows)

**Recommendation**: Start with Option A (single view), add Option B for power users later.

### Broker Auth: OAuth in Sandbox Webview

```typescript
// Open broker OAuth in a sandboxed window
function openBrokerLogin(broker: string, oauthUrl: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const loginWindow = new BrowserWindow({
      title: `Login - ${broker}`,
      url: oauthUrl,
      sandbox: true,
      frame: { x: 200, y: 200, width: 500, height: 700 },
      navigationRules: `https://*.${broker}.com/*,https://*.kite.trade/*,^*`,
    });

    loginWindow.webview.on("will-navigate", (e) => {
      const url = new URL(e.data.detail);
      // Check for OAuth callback with request_token
      if (url.searchParams.has("request_token")) {
        resolve(url.searchParams.get("request_token")!);
        loginWindow.close();
      }
    });

    loginWindow.on("close", () => reject(new Error("Login cancelled")));
  });
}
```

### Environment Variables → Encrypted Config

OpenAlgo uses `.env` with 100+ variables. In Electrobun, store config encrypted in SQLite:

```typescript
// First run: prompt user for broker credentials
// Store encrypted in SQLite
// No .env file needed — desktop app manages its own config
const config = new SecureConfig(`${Utils.paths.userData}/config.db`, masterKey);
config.set("broker.zerodha.api_key", apiKey);
config.set("broker.zerodha.api_secret", apiSecret);
```
