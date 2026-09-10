---
name: electrobun
description: Build large-scale algo trading desktop apps with Electrobun. Use when creating desktop apps, trading platforms, broker integrations, market data UIs, WebSocket connections, authentication systems, or any Electrobun project. Covers architecture, security, performance, storage, RPC, and all Electrobun APIs.
user-invocable: true
argument-hint: [feature-or-component]
allowed-tools: Read, Grep, Glob, Bash, Write, Edit
---

# Electrobun Algo Trading Application Skill

Build production-grade algo trading desktop applications with Electrobun + Bun runtime. This skill encodes best practices for security, performance, broker integration, real-time data, and the complete Electrobun API surface.

## Quick Reference

For detailed guides on specific topics, see:
- [architecture.md](architecture.md) - Application architecture and project structure
- [api-reference.md](api-reference.md) - Complete Electrobun API reference (all classes, methods, events)
- [security.md](security.md) - Authentication, encryption, API key management, sandboxing
- [broker-integration.md](broker-integration.md) - Broker APIs, order management, position tracking
- [websockets-realtime.md](websockets-realtime.md) - WebSocket connections, market data streaming, reconnection
- [storage.md](storage.md) - SQLite, time-series data, trade logging, caching
- [performance.md](performance.md) - Memory efficiency, GC tuning, data streaming, profiling

### OpenAlgo Migration & Advanced Features
- [openalgo-migration.md](openalgo-migration.md) - Full OpenAlgo → Electrobun migration guide, RPC schema, phased plan
- [broker-plugin-system.md](broker-plugin-system.md) - 29-broker plugin architecture, registry, symbol mapping
- [options-analytics.md](options-analytics.md) - Greeks (Black-Scholes), IV, max pain, PCR, GEX, straddle pricing
- [strategy-execution.md](strategy-execution.md) - Python strategy subprocess, flow engine, webhooks, action center, Telegram, market calendar
- [sandbox-paper-trading.md](sandbox-paper-trading.md) - Paper trading engine, virtual capital, margin simulation, order routing
- [monitoring-logging.md](monitoring-logging.md) - Structured logging, traffic tracking, latency monitoring, health checks, system metrics

## Core Principles

1. **Bun is the backend** - All sensitive logic (broker keys, order execution, DB access) runs in the Bun main process. The webview is ONLY for UI rendering.
2. **Type-safe RPC** - All communication between Bun and webview uses `defineRPC` with strict TypeScript schemas. Never pass raw strings or untyped data.
3. **Sandbox untrusted content** - Any external content (broker login pages, charts from third-party) must use `sandbox: true` or separate `<electrobun-webview>` with navigation rules.
4. **No secrets in the webview** - API keys, tokens, database connections - all stay in `src/bun/`. The webview calls Bun RPC handlers which proxy all sensitive operations.
5. **Fail-safe order management** - Every order operation must have timeout handling, duplicate prevention, and state reconciliation.

## Project Structure for Trading Apps

```
trading-app/
  electrobun.config.ts
  package.json
  src/
    bun/
      index.ts                 # App entry, window creation, menu setup
      broker/
        interface.ts           # Abstract broker interface
        zerodha.ts             # Zerodha/Kite implementation
        types.ts               # Order, Position, Holding types
      api/
        server.ts              # Local HTTP/WebSocket API server
        auth.ts                # Authentication middleware
        routes.ts              # API route handlers
      db/
        index.ts               # Database initialization
        migrations.ts          # Schema migrations
        queries.ts             # Prepared statements
      services/
        order-manager.ts       # Order lifecycle management
        position-tracker.ts    # Real-time position tracking
        market-data.ts         # WebSocket market data handler
        risk-manager.ts        # Pre-trade risk checks
        strategy-engine.ts     # Strategy execution
      utils/
        logger.ts              # Structured logging
        crypto.ts              # Encryption utilities
        config.ts              # App configuration
    mainview/
      index.html               # Main UI shell
      index.css                # Global styles
      index.ts                 # Electroview + RPC setup
    dashboard/
      index.html               # Dashboard view
      index.ts                 # Dashboard logic
      index.css
    orderbook/
      index.html               # Order book view
      index.ts
      index.css
```

## electrobun.config.ts Template

```typescript
import type { ElectrobunConfig } from "electrobun";
import pkg from "./package.json";

export default {
  app: {
    name: "Trading Platform",
    identifier: "com.yourcompany.trading",
    version: pkg.version,
  },
  build: {
    bun: {
      entrypoint: "src/bun/index.ts",
    },
    views: {
      mainview: { entrypoint: "src/mainview/index.ts" },
      dashboard: { entrypoint: "src/dashboard/index.ts" },
      orderbook: { entrypoint: "src/orderbook/index.ts" },
    },
    copy: {
      "src/mainview/index.html": "views/mainview/index.html",
      "src/mainview/index.css": "views/mainview/index.css",
      "src/dashboard/index.html": "views/dashboard/index.html",
      "src/dashboard/index.css": "views/dashboard/index.css",
      "src/orderbook/index.html": "views/orderbook/index.html",
      "src/orderbook/index.css": "views/orderbook/index.css",
    },
    mac: { bundleCEF: false, codesign: true, notarize: true },
    linux: { bundleCEF: false },
    win: { bundleCEF: false },
  },
  runtime: {
    exitOnLastWindowClosed: true,
  },
  release: {
    baseUrl: "https://your-update-server.com/releases",
    generatePatch: true,
  },
} satisfies ElectrobunConfig;
```

## RPC Schema Pattern for Trading

```typescript
import type { RPCSchema } from "electrobun/bun";

// Define in a shared types file, import on both sides
type TradingRPC = {
  bun: RPCSchema<{
    requests: {
      // Authentication
      login: { params: { apiKey: string; apiSecret: string; totp?: string }; response: { success: boolean; error?: string } };
      logout: { params: {}; response: { success: boolean } };
      getAuthState: { params: {}; response: { authenticated: boolean; broker: string } };

      // Market Data
      getLTP: { params: { symbols: string[] }; response: Record<string, number> };
      getQuote: { params: { symbol: string }; response: QuoteData };
      getHistorical: { params: { symbol: string; from: string; to: string; interval: string }; response: OHLC[] };

      // Orders
      placeOrder: { params: OrderParams; response: { orderId: string; status: string } };
      modifyOrder: { params: ModifyOrderParams; response: { success: boolean } };
      cancelOrder: { params: { orderId: string }; response: { success: boolean } };
      getOrders: { params: {}; response: Order[] };
      getOrderHistory: { params: { orderId: string }; response: OrderUpdate[] };

      // Portfolio
      getPositions: { params: {}; response: Position[] };
      getHoldings: { params: {}; response: Holding[] };
      getMargins: { params: {}; response: MarginData };

      // Strategy
      startStrategy: { params: { strategyId: string; config: StrategyConfig }; response: { success: boolean } };
      stopStrategy: { params: { strategyId: string }; response: { success: boolean } };
      getStrategyState: { params: { strategyId: string }; response: StrategyState };
    };
    messages: {
      logMessage: { level: string; message: string; context?: string };
    };
  }>;
  webview: RPCSchema<{
    requests: {};
    messages: {
      // Real-time updates pushed from Bun to UI
      tickUpdate: { symbol: string; ltp: number; change: number; volume: number };
      orderUpdate: { orderId: string; status: string; message: string };
      positionUpdate: { positions: Position[] };
      strategyLog: { strategyId: string; message: string; level: string };
      connectionStatus: { broker: string; status: "connected" | "disconnected" | "reconnecting" };
    };
  }>;
};
```

## Main Process Entry Pattern

```typescript
// src/bun/index.ts
import {
  ApplicationMenu, BrowserView, BrowserWindow, Tray, Updater, Utils
} from "electrobun/bun";
import type { TradingRPC } from "./types";
import { initDatabase } from "./db";
import { createBrokerConnection } from "./broker/interface";
import { startMarketDataStream } from "./services/market-data";
import { OrderManager } from "./services/order-manager";

// Initialize database
const db = initDatabase();
const broker = createBrokerConnection(db);
const orderManager = new OrderManager(broker, db);

// Define RPC
const rpc = BrowserView.defineRPC<TradingRPC>({
  maxRequestTime: 30000,
  handlers: {
    requests: {
      login: async (params) => broker.authenticate(params),
      placeOrder: async (params) => orderManager.place(params),
      cancelOrder: async (params) => orderManager.cancel(params.orderId),
      getPositions: async () => broker.getPositions(),
      getOrders: async () => broker.getOrders(),
      getMargins: async () => broker.getMargins(),
      getLTP: async ({ symbols }) => broker.getLTP(symbols),
      // ... all handlers
    },
    messages: {
      logMessage: ({ level, message }) => console.log(`[${level}] ${message}`),
    },
  },
});

// Create main window
const mainWindow = new BrowserWindow({
  title: "Trading Platform",
  url: "views://mainview/index.html",
  frame: { x: 100, y: 100, width: 1400, height: 900 },
  rpc,
});

// Push real-time data to UI
broker.on("tick", (data) => {
  mainWindow.webview.rpc?.send.tickUpdate(data);
});
broker.on("order-update", (data) => {
  mainWindow.webview.rpc?.send.orderUpdate(data);
});

// System tray for background operation
const tray = new Tray({ title: "Trading", template: true });
tray.setMenu([
  { label: "Show Window", action: "show" },
  { label: "Connection Status", action: "status" },
  { type: "separator" },
  { label: "Emergency Stop All", action: "emergency-stop" },
  { type: "separator" },
  { label: "Quit", action: "quit" },
]);
tray.on("tray-clicked", async (e) => {
  if (e.data.action === "show") mainWindow.show();
  if (e.data.action === "emergency-stop") await orderManager.cancelAll();
  if (e.data.action === "quit") Utils.quit();
});

// Graceful shutdown
mainWindow.on("close", async () => {
  await broker.disconnect();
  db.close();
  Utils.quit();
});
```

## Webview Entry Pattern

```typescript
// src/mainview/index.ts
import { Electroview, type RPCSchema } from "electrobun/view";
import type { TradingRPC } from "../shared/types";

const rpc = Electroview.defineRPC<TradingRPC>({
  maxRequestTime: 30000,
  handlers: {
    requests: {},
    messages: {
      tickUpdate: (data) => updateTickerUI(data),
      orderUpdate: (data) => updateOrderPanel(data),
      positionUpdate: (data) => updatePositionTable(data),
      connectionStatus: (data) => updateStatusBar(data),
      strategyLog: (data) => appendStrategyLog(data),
    },
  },
});

const _ev = new Electroview({ rpc });

// All data fetching goes through RPC - never direct API calls from webview
async function init() {
  const auth = await rpc.request.getAuthState({});
  if (!auth.authenticated) showLoginScreen();
  else loadDashboard();
}

async function loadDashboard() {
  const [positions, orders, margins] = await Promise.all([
    rpc.request.getPositions({}),
    rpc.request.getOrders({}),
    rpc.request.getMargins({}),
  ]);
  renderDashboard({ positions, orders, margins });
}

init();
```

## Critical Rules for $ARGUMENTS

When building features, ALWAYS:

1. **Proxy all broker API calls through Bun RPC** - never call broker APIs from webview
2. **Validate order parameters** in Bun before sending to broker (quantity > 0, valid price, valid symbol)
3. **Use prepared SQLite statements** - never string-concatenate SQL
4. **Implement circuit breakers** - max orders/minute, max loss limits, position size limits
5. **Log every order action** to SQLite with timestamps for audit trail
6. **Handle WebSocket reconnection** with exponential backoff and state reconciliation
7. **Encrypt sensitive config** (API keys) at rest using `bun:crypto`
8. **Use navigation rules** to restrict webview to only `views://*` URLs
9. **Use `sandbox: true`** for any window loading external broker OAuth pages
10. **Run market data processing in Bun** - send only UI-ready data to webview via RPC messages
