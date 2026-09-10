# Sandbox / Paper Trading System for Electrobun

Replicates OpenAlgo's full paper trading environment with virtual capital, margin simulation, and automatic square-off.

## Architecture

```
┌─────────────────────────────────────────┐
│  App Mode Toggle (Live / Sandbox)       │
│  Webview sends: rpc.request.toggleSandbox()
└───────────────────┬─────────────────────┘
                    │
┌───────────────────▼─────────────────────┐
│  Order Router (src/bun/services/)       │
│  if (sandboxMode) → SandboxEngine       │
│  else → LiveBroker                      │
└───────────────────┬─────────────────────┘
                    │
┌───────────────────▼─────────────────────┐
│  SandboxEngine (src/bun/sandbox/)       │
│  ┌──────────┐ ┌──────────┐ ┌────────┐  │
│  │ Orders   │ │ Positions│ │ Funds  │  │
│  │ Manager  │ │ Manager  │ │ Manager│  │
│  └──────────┘ └──────────┘ └────────┘  │
│  ┌──────────────────────────────────┐   │
│  │  Market Data Feed (real prices)  │   │
│  └──────────────────────────────────┘   │
│  ┌──────────────────────────────────┐   │
│  │  SQLite: sandbox.db              │   │
│  └──────────────────────────────────┘   │
└─────────────────────────────────────────┘
```

## Sandbox Database Schema

```typescript
// src/bun/sandbox/db.ts
import { Database } from "bun:sqlite";
import { Utils } from "electrobun/bun";

export function initSandboxDB(): Database {
  const db = new Database(`${Utils.paths.userData}/sandbox.db`);
  db.run("PRAGMA journal_mode = WAL");
  db.run("PRAGMA foreign_keys = ON");

  db.run(`
    CREATE TABLE IF NOT EXISTS sandbox_orders (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      order_id TEXT NOT NULL UNIQUE,
      symbol TEXT NOT NULL,
      exchange TEXT NOT NULL,
      transaction_type TEXT NOT NULL,
      quantity INTEGER NOT NULL,
      filled_quantity INTEGER DEFAULT 0,
      price REAL,
      trigger_price REAL,
      average_price REAL DEFAULT 0,
      order_type TEXT NOT NULL,
      product TEXT NOT NULL,
      variety TEXT DEFAULT 'regular',
      status TEXT NOT NULL DEFAULT 'OPEN',
      status_message TEXT,
      tag TEXT,
      placed_at INTEGER NOT NULL,
      updated_at INTEGER NOT NULL
    )
  `);

  db.run(`
    CREATE TABLE IF NOT EXISTS sandbox_trades (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      trade_id TEXT NOT NULL,
      order_id TEXT NOT NULL,
      symbol TEXT NOT NULL,
      exchange TEXT NOT NULL,
      transaction_type TEXT NOT NULL,
      quantity INTEGER NOT NULL,
      price REAL NOT NULL,
      product TEXT NOT NULL,
      traded_at INTEGER NOT NULL
    )
  `);

  db.run(`
    CREATE TABLE IF NOT EXISTS sandbox_positions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      symbol TEXT NOT NULL,
      exchange TEXT NOT NULL,
      product TEXT NOT NULL,
      quantity INTEGER NOT NULL DEFAULT 0,
      buy_quantity INTEGER NOT NULL DEFAULT 0,
      sell_quantity INTEGER NOT NULL DEFAULT 0,
      buy_value REAL NOT NULL DEFAULT 0,
      sell_value REAL NOT NULL DEFAULT 0,
      average_price REAL NOT NULL DEFAULT 0,
      last_price REAL NOT NULL DEFAULT 0,
      pnl REAL NOT NULL DEFAULT 0,
      realized_pnl REAL NOT NULL DEFAULT 0,
      unrealized_pnl REAL NOT NULL DEFAULT 0,
      updated_at INTEGER NOT NULL,
      UNIQUE(symbol, exchange, product)
    )
  `);

  db.run(`
    CREATE TABLE IF NOT EXISTS sandbox_funds (
      id INTEGER PRIMARY KEY DEFAULT 1,
      initial_capital REAL NOT NULL DEFAULT 10000000,
      available_cash REAL NOT NULL DEFAULT 10000000,
      used_margin REAL NOT NULL DEFAULT 0,
      realized_pnl REAL NOT NULL DEFAULT 0,
      unrealized_pnl REAL NOT NULL DEFAULT 0,
      updated_at INTEGER NOT NULL
    )
  `);

  // Initialize funds if empty
  const funds = db.query("SELECT * FROM sandbox_funds WHERE id = 1").get();
  if (!funds) {
    db.run("INSERT INTO sandbox_funds (initial_capital, available_cash, updated_at) VALUES (10000000, 10000000, ?)", [Date.now()]);
  }

  return db;
}
```

## Sandbox Engine

```typescript
// src/bun/sandbox/engine.ts
import { Database } from "bun:sqlite";
import type { OrderParams, Position, Order, Trade, FundsData } from "../broker/types";

interface MarketDataProvider {
  getLTP(symbol: string, exchange: string): number | null;
  getDepth(symbol: string, exchange: string): { bestBid: number; bestAsk: number } | null;
}

export class SandboxEngine {
  private db: Database;
  private marketData: MarketDataProvider;
  private nextOrderId = 1;
  private nextTradeId = 1;
  private listeners = new Map<string, Set<Function>>();

  // Margin multipliers per product
  private marginMultiplier: Record<string, number> = {
    MIS: 5,    // 5x leverage for intraday
    NRML: 1,   // No leverage for carry-forward
    CNC: 1,    // No leverage for delivery
  };

  constructor(db: Database, marketData: MarketDataProvider) {
    this.db = db;
    this.marketData = marketData;

    // Load last order/trade IDs
    const lastOrder = db.query("SELECT MAX(CAST(order_id AS INTEGER)) as max_id FROM sandbox_orders").get() as any;
    this.nextOrderId = (lastOrder?.max_id || 0) + 1;
    const lastTrade = db.query("SELECT MAX(CAST(trade_id AS INTEGER)) as max_id FROM sandbox_trades").get() as any;
    this.nextTradeId = (lastTrade?.max_id || 0) + 1;
  }

  async placeOrder(params: OrderParams): Promise<{ orderId: string; status: string }> {
    const orderId = String(this.nextOrderId++);
    const now = Date.now();

    // Check margin
    const marginRequired = this.calculateMarginRequired(params);
    const funds = this.getFunds();
    if (marginRequired > funds.available_cash) {
      return { orderId, status: "REJECTED" };
    }

    // Insert order
    this.db.run(`
      INSERT INTO sandbox_orders (order_id, symbol, exchange, transaction_type, quantity, price,
        trigger_price, order_type, product, variety, status, tag, placed_at, updated_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `, [orderId, params.symbol, params.exchange, params.transactionType, params.quantity,
        params.price || 0, params.triggerPrice || 0, params.orderType, params.product,
        params.variety || "regular", "OPEN", params.tag || null, now, now]);

    // Try immediate execution for market orders
    if (params.orderType === "MARKET") {
      this.executeMarketOrder(orderId, params);
    } else if (params.orderType === "LIMIT") {
      // Check if limit price is already favorable
      this.checkLimitOrder(orderId, params);
    }

    // Block margin
    this.updateMargin(marginRequired, "block");

    this.emit("order", { type: "placed", orderId, params });
    return { orderId, status: "OPEN" };
  }

  private executeMarketOrder(orderId: string, params: OrderParams): void {
    const depth = this.marketData.getDepth(params.symbol, params.exchange);
    const ltp = this.marketData.getLTP(params.symbol, params.exchange);

    let executionPrice: number;
    if (depth) {
      executionPrice = params.transactionType === "BUY" ? depth.bestAsk : depth.bestBid;
    } else if (ltp) {
      // Simulate slippage: 0.05% for market orders
      const slippage = ltp * 0.0005;
      executionPrice = params.transactionType === "BUY" ? ltp + slippage : ltp - slippage;
    } else {
      this.updateOrderStatus(orderId, "REJECTED", "No market data available");
      return;
    }

    this.executeTrade(orderId, params, executionPrice);
  }

  private checkLimitOrder(orderId: string, params: OrderParams): void {
    const ltp = this.marketData.getLTP(params.symbol, params.exchange);
    if (!ltp || !params.price) return;

    if (params.transactionType === "BUY" && ltp <= params.price) {
      this.executeTrade(orderId, params, params.price);
    } else if (params.transactionType === "SELL" && ltp >= params.price) {
      this.executeTrade(orderId, params, params.price);
    }
    // Otherwise stays OPEN — will be checked on each tick
  }

  private executeTrade(orderId: string, params: OrderParams, price: number): void {
    const tradeId = String(this.nextTradeId++);
    const now = Date.now();

    // Record trade
    this.db.run(`
      INSERT INTO sandbox_trades (trade_id, order_id, symbol, exchange, transaction_type, quantity, price, product, traded_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    `, [tradeId, orderId, params.symbol, params.exchange, params.transactionType, params.quantity, price, params.product, now]);

    // Update order
    this.db.run(`
      UPDATE sandbox_orders SET status = 'COMPLETE', filled_quantity = quantity, average_price = ?, updated_at = ?
      WHERE order_id = ?
    `, [price, now, orderId]);

    // Update position
    this.updatePosition(params.symbol, params.exchange, params.product, params.transactionType, params.quantity, price);

    // Release blocked margin and recalculate
    this.recalculateFunds();

    this.emit("trade", { tradeId, orderId, symbol: params.symbol, price, quantity: params.quantity });
    this.emit("order", { type: "filled", orderId });
  }

  private updatePosition(symbol: string, exchange: string, product: string, side: string, qty: number, price: number): void {
    const existing = this.db.query(
      "SELECT * FROM sandbox_positions WHERE symbol = ? AND exchange = ? AND product = ?"
    ).get(symbol, exchange, product) as any;

    if (!existing) {
      const netQty = side === "BUY" ? qty : -qty;
      const buyQty = side === "BUY" ? qty : 0;
      const sellQty = side === "SELL" ? qty : 0;
      const buyVal = side === "BUY" ? qty * price : 0;
      const sellVal = side === "SELL" ? qty * price : 0;

      this.db.run(`
        INSERT INTO sandbox_positions (symbol, exchange, product, quantity, buy_quantity, sell_quantity,
          buy_value, sell_value, average_price, last_price, pnl, realized_pnl, unrealized_pnl, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, ?)
      `, [symbol, exchange, product, netQty, buyQty, sellQty, buyVal, sellVal, price, price, Date.now()]);
    } else {
      const newBuyQty = existing.buy_quantity + (side === "BUY" ? qty : 0);
      const newSellQty = existing.sell_quantity + (side === "SELL" ? qty : 0);
      const newBuyVal = existing.buy_value + (side === "BUY" ? qty * price : 0);
      const newSellVal = existing.sell_value + (side === "SELL" ? qty * price : 0);
      const newNetQty = newBuyQty - newSellQty;

      // Calculate realized P&L on position reduction
      let realizedPnl = existing.realized_pnl;
      if (
        (existing.quantity > 0 && side === "SELL") ||
        (existing.quantity < 0 && side === "BUY")
      ) {
        const closedQty = Math.min(qty, Math.abs(existing.quantity));
        realizedPnl += closedQty * (price - existing.average_price) * (existing.quantity > 0 ? 1 : -1);
      }

      const avgPrice = newNetQty !== 0
        ? (side === "BUY" ? newBuyVal / newBuyQty : newSellVal / newSellQty)
        : 0;

      this.db.run(`
        UPDATE sandbox_positions SET quantity = ?, buy_quantity = ?, sell_quantity = ?,
          buy_value = ?, sell_value = ?, average_price = ?, realized_pnl = ?, updated_at = ?
        WHERE symbol = ? AND exchange = ? AND product = ?
      `, [newNetQty, newBuyQty, newSellQty, newBuyVal, newSellVal, avgPrice, realizedPnl, Date.now(), symbol, exchange, product]);
    }
  }

  // Call on each tick to check pending limit/SL orders
  onTick(symbol: string, exchange: string, ltp: number): void {
    // Update position last price
    this.db.run(
      "UPDATE sandbox_positions SET last_price = ?, unrealized_pnl = (? - average_price) * quantity, updated_at = ? WHERE symbol = ? AND exchange = ?",
      [ltp, ltp, Date.now(), symbol, exchange]
    );

    // Check pending limit orders
    const pendingOrders = this.db.query(
      "SELECT * FROM sandbox_orders WHERE symbol = ? AND exchange = ? AND status = 'OPEN'"
    ).all(symbol, exchange) as any[];

    for (const order of pendingOrders) {
      if (order.order_type === "LIMIT") {
        if (order.transaction_type === "BUY" && ltp <= order.price) {
          this.executeTrade(order.order_id, order, order.price);
        } else if (order.transaction_type === "SELL" && ltp >= order.price) {
          this.executeTrade(order.order_id, order, order.price);
        }
      } else if (order.order_type === "SL" || order.order_type === "SL-M") {
        if (order.transaction_type === "BUY" && ltp >= order.trigger_price) {
          const execPrice = order.order_type === "SL" ? order.price : ltp;
          this.executeTrade(order.order_id, order, execPrice);
        } else if (order.transaction_type === "SELL" && ltp <= order.trigger_price) {
          const execPrice = order.order_type === "SL" ? order.price : ltp;
          this.executeTrade(order.order_id, order, execPrice);
        }
      }
    }
  }

  // Auto square-off at market close (3:15 PM for equity, 11:30 PM for MCX)
  async autoSquareOff(exchange: string): Promise<{ closed: number }> {
    const positions = this.db.query(
      "SELECT * FROM sandbox_positions WHERE exchange = ? AND quantity != 0 AND product = 'MIS'"
    ).all(exchange) as any[];

    let closed = 0;
    for (const pos of positions) {
      const ltp = this.marketData.getLTP(pos.symbol, pos.exchange);
      if (!ltp) continue;

      await this.placeOrder({
        symbol: pos.symbol,
        exchange: pos.exchange,
        transactionType: pos.quantity > 0 ? "SELL" : "BUY",
        quantity: Math.abs(pos.quantity),
        orderType: "MARKET",
        product: "MIS",
      });
      closed++;
    }

    return { closed };
  }

  reset(): void {
    this.db.run("DELETE FROM sandbox_orders");
    this.db.run("DELETE FROM sandbox_trades");
    this.db.run("DELETE FROM sandbox_positions");
    this.db.run("UPDATE sandbox_funds SET available_cash = initial_capital, used_margin = 0, realized_pnl = 0, unrealized_pnl = 0, updated_at = ?", [Date.now()]);
  }

  // --- Query methods ---

  getOrders(): any[] { return this.db.query("SELECT * FROM sandbox_orders ORDER BY placed_at DESC").all(); }
  getTrades(): any[] { return this.db.query("SELECT * FROM sandbox_trades ORDER BY traded_at DESC").all(); }
  getPositions(): any[] { return this.db.query("SELECT * FROM sandbox_positions WHERE quantity != 0").all(); }
  getFunds(): any { return this.db.query("SELECT * FROM sandbox_funds WHERE id = 1").get(); }

  private calculateMarginRequired(params: OrderParams): number {
    const ltp = this.marketData.getLTP(params.symbol, params.exchange) || params.price || 0;
    const value = ltp * params.quantity;
    const multiplier = this.marginMultiplier[params.product] || 1;
    return value / multiplier;
  }

  private updateMargin(amount: number, action: "block" | "release"): void {
    const sign = action === "block" ? 1 : -1;
    this.db.run(
      "UPDATE sandbox_funds SET used_margin = used_margin + ?, available_cash = available_cash - ?, updated_at = ? WHERE id = 1",
      [amount * sign, amount * sign, Date.now()]
    );
  }

  private recalculateFunds(): void {
    const positions = this.db.query("SELECT * FROM sandbox_positions").all() as any[];
    let totalUnrealized = 0;
    let totalRealized = 0;
    let totalMargin = 0;

    for (const pos of positions) {
      totalUnrealized += pos.unrealized_pnl;
      totalRealized += pos.realized_pnl;
      if (pos.quantity !== 0) {
        totalMargin += Math.abs(pos.quantity) * pos.average_price / (this.marginMultiplier[pos.product] || 1);
      }
    }

    const funds = this.getFunds();
    const availableCash = funds.initial_capital + totalRealized - totalMargin;

    this.db.run(
      "UPDATE sandbox_funds SET available_cash = ?, used_margin = ?, realized_pnl = ?, unrealized_pnl = ?, updated_at = ? WHERE id = 1",
      [availableCash, totalMargin, totalRealized, totalUnrealized, Date.now()]
    );
  }

  private updateOrderStatus(orderId: string, status: string, message: string): void {
    this.db.run("UPDATE sandbox_orders SET status = ?, status_message = ?, updated_at = ? WHERE order_id = ?",
      [status, message, Date.now(), orderId]);
  }

  private emit(event: string, data: any): void {
    this.listeners.get(event)?.forEach((h) => h(data));
  }

  on(event: string, handler: Function): void {
    if (!this.listeners.has(event)) this.listeners.set(event, new Set());
    this.listeners.get(event)!.add(handler);
  }
}
```

## Order Routing: Live vs Sandbox

```typescript
// src/bun/services/order-router.ts
import type { OrderParams } from "../broker/types";
import type { BrokerPlugin } from "../broker/types";
import { SandboxEngine } from "../sandbox/engine";

export class OrderRouter {
  private sandboxMode = false;

  constructor(
    private broker: BrokerPlugin,
    private sandbox: SandboxEngine,
    private tokens: AuthTokens,
    private orderMode: "auto" | "semi_auto" = "auto"
  ) {}

  setSandboxMode(enabled: boolean): void {
    this.sandboxMode = enabled;
  }

  async placeOrder(params: OrderParams): Promise<OrderResult> {
    if (this.sandboxMode) {
      return this.sandbox.placeOrder(params);
    }

    if (this.orderMode === "semi_auto") {
      return this.routeToActionCenter(params);
    }

    return this.broker.placeOrder(
      this.mapToBrokerParams(params),
      this.tokens
    );
  }

  private routeToActionCenter(params: OrderParams): OrderResult {
    // Store as pending order, emit event for UI approval
    // ... (see action center section)
  }
}
```

## Square-Off Scheduler

```typescript
// src/bun/sandbox/squareoff-scheduler.ts
export class SquareOffScheduler {
  private timers: Timer[] = [];

  constructor(private sandbox: SandboxEngine) {}

  start(): void {
    // Check every minute during market hours
    const timer = setInterval(() => {
      const now = new Date();
      const hours = now.getHours();
      const minutes = now.getMinutes();

      // NSE/BSE equity: 3:15 PM IST
      if (hours === 15 && minutes === 15) {
        this.sandbox.autoSquareOff("NSE");
        this.sandbox.autoSquareOff("BSE");
      }

      // NFO: 3:25 PM IST
      if (hours === 15 && minutes === 25) {
        this.sandbox.autoSquareOff("NFO");
      }

      // MCX: 11:25 PM IST
      if (hours === 23 && minutes === 25) {
        this.sandbox.autoSquareOff("MCX");
      }
    }, 60000);

    this.timers.push(timer);
  }

  stop(): void {
    this.timers.forEach(clearInterval);
    this.timers = [];
  }
}
```
