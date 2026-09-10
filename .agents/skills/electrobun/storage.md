# Storage Patterns for Electrobun Trading Apps

## SQLite with Bun — Primary Storage

Bun has built-in SQLite (`bun:sqlite`) which is fast, ACID-compliant, and requires no external dependencies. Use it for all persistent data.

### Database Initialization

```typescript
// src/bun/db/index.ts
import { Database } from "bun:sqlite";
import { Utils } from "electrobun/bun";

export function initDatabase(): Database {
  const dbDir = Utils.paths.userData;
  // Ensure directory exists
  const fs = require("fs");
  if (!fs.existsSync(dbDir)) fs.mkdirSync(dbDir, { recursive: true });

  const dbPath = `${dbDir}/trading.db`;
  const db = new Database(dbPath);

  // WAL mode for better concurrent read/write performance
  db.run("PRAGMA journal_mode = WAL");
  db.run("PRAGMA synchronous = NORMAL");
  db.run("PRAGMA cache_size = -64000"); // 64MB cache
  db.run("PRAGMA foreign_keys = ON");
  db.run("PRAGMA busy_timeout = 5000");

  // Run migrations
  migrate(db);

  return db;
}
```

### Schema Migrations

```typescript
// src/bun/db/migrations.ts
import { Database } from "bun:sqlite";

const MIGRATIONS = [
  // Migration 1: Core tables
  `
  CREATE TABLE IF NOT EXISTS migrations (
    id INTEGER PRIMARY KEY,
    applied_at INTEGER NOT NULL
  );

  CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL UNIQUE,
    broker TEXT NOT NULL,
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL,
    transaction_type TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    filled_quantity INTEGER DEFAULT 0,
    price REAL,
    trigger_price REAL,
    average_price REAL,
    order_type TEXT NOT NULL,
    product TEXT NOT NULL,
    variety TEXT DEFAULT 'regular',
    status TEXT NOT NULL,
    status_message TEXT,
    tag TEXT,
    strategy_id TEXT,
    placed_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
  );

  CREATE INDEX IF NOT EXISTS idx_orders_symbol ON orders(symbol);
  CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
  CREATE INDEX IF NOT EXISTS idx_orders_placed_at ON orders(placed_at);
  CREATE INDEX IF NOT EXISTS idx_orders_strategy ON orders(strategy_id);
  `,

  // Migration 2: Trade log and positions
  `
  CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id TEXT NOT NULL,
    order_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL,
    transaction_type TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    price REAL NOT NULL,
    product TEXT NOT NULL,
    traded_at INTEGER NOT NULL,
    FOREIGN KEY (order_id) REFERENCES orders(order_id)
  );

  CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
  CREATE INDEX IF NOT EXISTS idx_trades_traded_at ON trades(traded_at);

  CREATE TABLE IF NOT EXISTS position_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    average_price REAL NOT NULL,
    last_price REAL NOT NULL,
    pnl REAL NOT NULL,
    snapshot_at INTEGER NOT NULL
  );

  CREATE INDEX IF NOT EXISTS idx_snapshots_symbol_time ON position_snapshots(symbol, snapshot_at);
  `,

  // Migration 3: OHLC cache and config
  `
  CREATE TABLE IF NOT EXISTS ohlc_cache (
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    timestamp INTEGER NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume INTEGER NOT NULL,
    oi INTEGER DEFAULT 0,
    PRIMARY KEY (symbol, interval, timestamp)
  );

  CREATE TABLE IF NOT EXISTS app_config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at INTEGER NOT NULL
  );

  CREATE TABLE IF NOT EXISTS secure_config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at INTEGER NOT NULL
  );
  `,

  // Migration 4: Audit log and strategy state
  `
  CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    action TEXT NOT NULL,
    details TEXT,
    result TEXT,
    error TEXT
  );

  CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp);
  CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action);

  CREATE TABLE IF NOT EXISTS strategy_state (
    strategy_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    config TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'stopped',
    last_run_at INTEGER,
    created_at INTEGER NOT NULL
  );

  CREATE TABLE IF NOT EXISTS strategy_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id TEXT NOT NULL,
    timestamp INTEGER NOT NULL,
    level TEXT NOT NULL,
    message TEXT NOT NULL,
    data TEXT,
    FOREIGN KEY (strategy_id) REFERENCES strategy_state(strategy_id)
  );

  CREATE INDEX IF NOT EXISTS idx_strategy_logs_time ON strategy_logs(strategy_id, timestamp);
  `,

  // Migration 5: Daily P&L tracking
  `
  CREATE TABLE IF NOT EXISTS daily_pnl (
    date TEXT NOT NULL,
    broker TEXT NOT NULL,
    realized_pnl REAL NOT NULL DEFAULT 0,
    unrealized_pnl REAL NOT NULL DEFAULT 0,
    charges REAL NOT NULL DEFAULT 0,
    net_pnl REAL NOT NULL DEFAULT 0,
    trades_count INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (date, broker)
  );
  `,
];

export function migrate(db: Database): void {
  db.run("CREATE TABLE IF NOT EXISTS migrations (id INTEGER PRIMARY KEY, applied_at INTEGER NOT NULL)");

  const applied = db.query("SELECT id FROM migrations ORDER BY id").all() as { id: number }[];
  const appliedSet = new Set(applied.map((r) => r.id));

  for (let i = 0; i < MIGRATIONS.length; i++) {
    if (appliedSet.has(i + 1)) continue;

    console.log(`Running migration ${i + 1}...`);
    db.run("BEGIN");
    try {
      // Split multi-statement SQL and run each
      const statements = MIGRATIONS[i].split(";").filter((s) => s.trim());
      for (const stmt of statements) {
        db.run(stmt);
      }
      db.run("INSERT INTO migrations (id, applied_at) VALUES (?, ?)", [i + 1, Date.now()]);
      db.run("COMMIT");
    } catch (e) {
      db.run("ROLLBACK");
      throw e;
    }
  }
}
```

### Prepared Statement Queries

```typescript
// src/bun/db/queries.ts
import { Database } from "bun:sqlite";

export class TradingQueries {
  private stmts: Record<string, any>;

  constructor(private db: Database) {
    // Prepare all statements once for performance
    this.stmts = {
      insertOrder: db.prepare(`
        INSERT INTO orders (order_id, broker, symbol, exchange, transaction_type,
          quantity, price, trigger_price, order_type, product, variety, status, tag,
          strategy_id, placed_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      `),

      updateOrderStatus: db.prepare(`
        UPDATE orders SET status = ?, status_message = ?, filled_quantity = ?,
          average_price = ?, updated_at = ? WHERE order_id = ?
      `),

      getOrdersByDate: db.prepare(`
        SELECT * FROM orders WHERE placed_at >= ? AND placed_at < ? ORDER BY placed_at DESC
      `),

      getOpenOrders: db.prepare(`
        SELECT * FROM orders WHERE status IN ('OPEN', 'TRIGGER_PENDING') ORDER BY placed_at DESC
      `),

      insertTrade: db.prepare(`
        INSERT INTO trades (trade_id, order_id, symbol, exchange, transaction_type,
          quantity, price, product, traded_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
      `),

      getTradesByDate: db.prepare(`
        SELECT * FROM trades WHERE traded_at >= ? AND traded_at < ? ORDER BY traded_at DESC
      `),

      insertOHLC: db.prepare(`
        INSERT OR REPLACE INTO ohlc_cache (symbol, interval, timestamp, open, high, low, close, volume, oi)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
      `),

      getOHLC: db.prepare(`
        SELECT * FROM ohlc_cache WHERE symbol = ? AND interval = ?
          AND timestamp >= ? AND timestamp <= ? ORDER BY timestamp ASC
      `),

      insertAudit: db.prepare(`
        INSERT INTO audit_log (timestamp, action, details, result, error) VALUES (?, ?, ?, ?, ?)
      `),

      getConfig: db.prepare("SELECT value FROM app_config WHERE key = ?"),
      setConfig: db.prepare("INSERT OR REPLACE INTO app_config (key, value, updated_at) VALUES (?, ?, ?)"),

      getDailyPnL: db.prepare("SELECT * FROM daily_pnl WHERE date = ? AND broker = ?"),
      upsertDailyPnL: db.prepare(`
        INSERT OR REPLACE INTO daily_pnl (date, broker, realized_pnl, unrealized_pnl,
          charges, net_pnl, trades_count, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
      `),

      insertPositionSnapshot: db.prepare(`
        INSERT INTO position_snapshots (symbol, exchange, quantity, average_price, last_price, pnl, snapshot_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
      `),
    };
  }

  // --- Orders ---

  saveOrder(order: any): void {
    this.stmts.insertOrder.run(
      order.orderId, order.broker, order.symbol, order.exchange,
      order.transactionType, order.quantity, order.price, order.triggerPrice,
      order.orderType, order.product, order.variety, order.status,
      order.tag, order.strategyId, order.placedAt, Date.now()
    );
  }

  updateOrderStatus(orderId: string, status: string, message: string, filled: number, avgPrice: number): void {
    this.stmts.updateOrderStatus.run(status, message, filled, avgPrice, Date.now(), orderId);
  }

  getOpenOrders(): any[] {
    return this.stmts.getOpenOrders.all();
  }

  getOrdersByDateRange(from: number, to: number): any[] {
    return this.stmts.getOrdersByDate.all(from, to);
  }

  // --- OHLC Cache ---

  cacheOHLC(symbol: string, interval: string, candles: any[]): void {
    const insertMany = this.db.transaction((data: any[]) => {
      for (const c of data) {
        this.stmts.insertOHLC.run(symbol, interval, c.timestamp, c.open, c.high, c.low, c.close, c.volume, c.oi || 0);
      }
    });
    insertMany(candles);
  }

  getCachedOHLC(symbol: string, interval: string, from: number, to: number): any[] {
    return this.stmts.getOHLC.all(symbol, interval, from, to);
  }

  // --- Config ---

  getConfig(key: string): string | null {
    const row = this.stmts.getConfig.get(key) as any;
    return row?.value ?? null;
  }

  setConfig(key: string, value: string): void {
    this.stmts.setConfig.run(key, value, Date.now());
  }

  // --- Audit ---

  audit(action: string, details: any, result?: any, error?: string): void {
    this.stmts.insertAudit.run(
      Date.now(), action,
      details ? JSON.stringify(details) : null,
      result ? JSON.stringify(result) : null,
      error ?? null
    );
  }

  // --- Daily P&L ---

  updateDailyPnL(date: string, broker: string, data: {
    realized: number; unrealized: number; charges: number; trades: number;
  }): void {
    const net = data.realized + data.unrealized - data.charges;
    this.stmts.upsertDailyPnL.run(
      date, broker, data.realized, data.unrealized, data.charges, net, data.trades, Date.now()
    );
  }
}
```

### Transaction Patterns

```typescript
// Use transactions for multi-step operations
function processOrderFill(db: Database, queries: TradingQueries, fill: any): void {
  const txn = db.transaction(() => {
    // Update order
    queries.updateOrderStatus(fill.orderId, fill.status, fill.message, fill.filled, fill.avgPrice);

    // Record trade
    queries.stmts.insertTrade.run(
      fill.tradeId, fill.orderId, fill.symbol, fill.exchange,
      fill.transactionType, fill.quantity, fill.price, fill.product, Date.now()
    );

    // Audit
    queries.audit("ORDER_FILL", fill);
  });

  txn(); // Execute transaction
}
```

## OHLC Data Caching Strategy

```typescript
// Cache historical data locally to avoid repeated API calls
class OHLCCacheManager {
  constructor(private queries: TradingQueries, private broker: BrokerConnection) {}

  async getCandles(
    symbol: string, interval: string, from: string, to: string
  ): Promise<OHLC[]> {
    const fromTs = new Date(from).getTime();
    const toTs = new Date(to).getTime();

    // Check cache first
    const cached = this.queries.getCachedOHLC(symbol, interval, fromTs, toTs);

    if (cached.length > 0) {
      const cachedFrom = cached[0].timestamp;
      const cachedTo = cached[cached.length - 1].timestamp;

      // If cache covers the full range, return it
      if (cachedFrom <= fromTs && cachedTo >= toTs - this.intervalToMs(interval)) {
        return cached;
      }
    }

    // Fetch from broker
    const candles = await this.broker.getHistorical({
      instrumentToken: await this.getToken(symbol),
      from, to, interval,
    });

    // Cache the results
    if (candles.length > 0) {
      this.queries.cacheOHLC(symbol, interval, candles.map(c => ({
        ...c,
        timestamp: new Date(c.timestamp).getTime(),
      })));
    }

    return candles;
  }

  private intervalToMs(interval: string): number {
    const map: Record<string, number> = {
      minute: 60000, "3minute": 180000, "5minute": 300000,
      "15minute": 900000, "30minute": 1800000, "60minute": 3600000,
      day: 86400000,
    };
    return map[interval] || 60000;
  }
}
```

## Database Maintenance

```typescript
// Run periodically to keep database performant
function maintenanceTask(db: Database): void {
  // Clean old audit logs (keep 30 days)
  const thirtyDaysAgo = Date.now() - 30 * 24 * 60 * 60 * 1000;
  db.run("DELETE FROM audit_log WHERE timestamp < ?", [thirtyDaysAgo]);

  // Clean old strategy logs (keep 7 days)
  const sevenDaysAgo = Date.now() - 7 * 24 * 60 * 60 * 1000;
  db.run("DELETE FROM strategy_logs WHERE timestamp < ?", [sevenDaysAgo]);

  // Clean old position snapshots (keep 90 days)
  const ninetyDaysAgo = Date.now() - 90 * 24 * 60 * 60 * 1000;
  db.run("DELETE FROM position_snapshots WHERE snapshot_at < ?", [ninetyDaysAgo]);

  // Clean old OHLC cache (keep 1 year of daily, 30 days of intraday)
  const oneYearAgo = Date.now() - 365 * 24 * 60 * 60 * 1000;
  db.run("DELETE FROM ohlc_cache WHERE interval = 'day' AND timestamp < ?", [oneYearAgo]);
  db.run("DELETE FROM ohlc_cache WHERE interval != 'day' AND timestamp < ?", [thirtyDaysAgo]);

  // Optimize database
  db.run("PRAGMA optimize");
}

// Schedule maintenance daily
setInterval(() => maintenanceTask(db), 24 * 60 * 60 * 1000);
```

## File-Based Export

```typescript
// Export trades to CSV for tax/compliance
async function exportTradesToCSV(db: Database, fromDate: string, toDate: string): Promise<string> {
  const from = new Date(fromDate).getTime();
  const to = new Date(toDate).getTime();

  const trades = db.query(`
    SELECT t.*, o.order_type, o.product, o.variety
    FROM trades t
    JOIN orders o ON t.order_id = o.order_id
    WHERE t.traded_at >= ? AND t.traded_at < ?
    ORDER BY t.traded_at ASC
  `).all(from, to) as any[];

  const header = "Date,Symbol,Exchange,Type,Qty,Price,OrderType,Product\n";
  const rows = trades.map(t =>
    `${new Date(t.traded_at).toISOString()},${t.symbol},${t.exchange},${t.transaction_type},${t.quantity},${t.price},${t.order_type},${t.product}`
  ).join("\n");

  const csv = header + rows;
  const filePath = `${Utils.paths.downloads}/trades_${fromDate}_${toDate}.csv`;
  await Bun.write(filePath, csv);
  Utils.showItemInFolder(filePath);
  return filePath;
}
```

## App Configuration Storage

```typescript
// Type-safe config with defaults
interface AppSettings {
  theme: "light" | "dark";
  defaultBroker: string;
  maxOrdersPerMinute: number;
  maxDailyLoss: number;
  maxPositionSize: number;
  watchlistSymbols: string[];
  apiServerPort: number;
  apiServerEnabled: boolean;
  autoSquareOffTime: string; // "15:15"
  notifications: { orders: boolean; pnl: boolean; errors: boolean };
}

const DEFAULT_SETTINGS: AppSettings = {
  theme: "dark",
  defaultBroker: "zerodha",
  maxOrdersPerMinute: 20,
  maxDailyLoss: 5000,
  maxPositionSize: 100,
  watchlistSymbols: ["NSE:NIFTY 50", "NSE:BANKNIFTY"],
  apiServerPort: 8765,
  apiServerEnabled: false,
  autoSquareOffTime: "15:15",
  notifications: { orders: true, pnl: true, errors: true },
};

class SettingsManager {
  constructor(private queries: TradingQueries) {}

  get<K extends keyof AppSettings>(key: K): AppSettings[K] {
    const raw = this.queries.getConfig(`settings.${key}`);
    if (raw === null) return DEFAULT_SETTINGS[key];
    return JSON.parse(raw);
  }

  set<K extends keyof AppSettings>(key: K, value: AppSettings[K]): void {
    this.queries.setConfig(`settings.${key}`, JSON.stringify(value));
  }

  getAll(): AppSettings {
    const settings = { ...DEFAULT_SETTINGS };
    for (const key of Object.keys(DEFAULT_SETTINGS) as (keyof AppSettings)[]) {
      const raw = this.queries.getConfig(`settings.${key}`);
      if (raw !== null) {
        (settings as any)[key] = JSON.parse(raw);
      }
    }
    return settings;
  }
}
```
