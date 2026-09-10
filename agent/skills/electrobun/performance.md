# Performance & Memory Efficiency for Electrobun Trading Apps

## Core Performance Principles

1. **Heavy computation in Bun** — All data processing (tick parsing, indicator calculation, risk checks) runs in the Bun process. The webview only renders pre-computed data.
2. **Throttle UI updates** — Market data arrives at 100s of ticks/second. Throttle RPC messages to 5-10 updates/sec per symbol.
3. **Use prepared statements** — Never build SQL strings dynamically.
4. **Avoid object churn** — Reuse buffers and objects in hot paths (tick processing).
5. **Batch operations** — Batch database writes, batch UI updates.

## Tick Processing Performance

### Object Pool for Tick Data

```typescript
// Avoid GC pressure by reusing tick objects
class TickPool {
  private pool: TickData[] = [];
  private index = 0;
  private size: number;

  constructor(size = 1000) {
    this.size = size;
    for (let i = 0; i < size; i++) {
      this.pool.push({
        symbol: "", exchange: "", lastPrice: 0,
        open: 0, high: 0, low: 0, close: 0,
        change: 0, changePercent: 0,
        volume: 0, buyQuantity: 0, sellQuantity: 0,
        ohlc: { open: 0, high: 0, low: 0, close: 0 },
        timestamp: 0,
      });
    }
  }

  acquire(): TickData {
    const tick = this.pool[this.index];
    this.index = (this.index + 1) % this.size;
    return tick;
  }
}

const tickPool = new TickPool(500);

// In binary parser — reuse objects instead of allocating new ones
function parseTick(view: DataView, offset: number): TickData {
  const tick = tickPool.acquire();
  tick.lastPrice = view.getInt32(offset + 4) / 100;
  tick.timestamp = Date.now();
  // ... fill other fields
  return tick;
}
```

### Throttled Broadcast to Webview

```typescript
// Don't send every tick to the webview — batch and throttle
class ThrottledBroadcaster {
  private pendingUpdates = new Map<string, TickData>();
  private timer: Timer | null = null;
  private intervalMs: number;

  constructor(
    private sendToWebview: (updates: Map<string, TickData>) => void,
    intervalMs = 200 // 5 updates per second
  ) {
    this.intervalMs = intervalMs;
  }

  queueUpdate(tick: TickData): void {
    this.pendingUpdates.set(tick.symbol, tick);

    if (!this.timer) {
      this.timer = setTimeout(() => {
        this.flush();
      }, this.intervalMs);
    }
  }

  private flush(): void {
    if (this.pendingUpdates.size > 0) {
      this.sendToWebview(this.pendingUpdates);
      this.pendingUpdates = new Map();
    }
    this.timer = null;
  }

  stop(): void {
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
  }
}

// Usage
const broadcaster = new ThrottledBroadcaster((updates) => {
  for (const [symbol, tick] of updates) {
    mainWindow.webview.rpc?.send.tickUpdate({
      symbol: tick.symbol,
      ltp: tick.lastPrice,
      change: tick.changePercent,
      volume: tick.volume,
    });
  }
});

broker.on("tick", (tick) => broadcaster.queueUpdate(tick));
```

## Memory-Efficient Data Structures

### Ring Buffer for Tick History

```typescript
// Fixed-size buffer that overwrites oldest entries — no array growth
class RingBuffer<T> {
  private buffer: T[];
  private head = 0;
  private count = 0;

  constructor(private capacity: number) {
    this.buffer = new Array(capacity);
  }

  push(item: T): void {
    this.buffer[this.head] = item;
    this.head = (this.head + 1) % this.capacity;
    if (this.count < this.capacity) this.count++;
  }

  // Get items in order (oldest first)
  toArray(): T[] {
    if (this.count < this.capacity) {
      return this.buffer.slice(0, this.count);
    }
    return [
      ...this.buffer.slice(this.head),
      ...this.buffer.slice(0, this.head),
    ];
  }

  get length(): number {
    return this.count;
  }

  last(): T | undefined {
    if (this.count === 0) return undefined;
    return this.buffer[(this.head - 1 + this.capacity) % this.capacity];
  }
}

// Keep last 5000 ticks per symbol without unbounded array growth
const tickHistory = new Map<string, RingBuffer<TickData>>();

function recordTick(tick: TickData): void {
  let buffer = tickHistory.get(tick.symbol);
  if (!buffer) {
    buffer = new RingBuffer(5000);
    tickHistory.set(tick.symbol, buffer);
  }
  buffer.push(tick);
}
```

### Typed Arrays for Numeric Data

```typescript
// Use Float64Array for OHLCV data instead of object arrays
class OHLCVSeries {
  private timestamps: Float64Array;
  private opens: Float64Array;
  private highs: Float64Array;
  private lows: Float64Array;
  private closes: Float64Array;
  private volumes: Float64Array;
  private length: number;

  constructor(capacity: number) {
    this.timestamps = new Float64Array(capacity);
    this.opens = new Float64Array(capacity);
    this.highs = new Float64Array(capacity);
    this.lows = new Float64Array(capacity);
    this.closes = new Float64Array(capacity);
    this.volumes = new Float64Array(capacity);
    this.length = 0;
  }

  push(ts: number, o: number, h: number, l: number, c: number, v: number): void {
    const i = this.length;
    this.timestamps[i] = ts;
    this.opens[i] = o;
    this.highs[i] = h;
    this.lows[i] = l;
    this.closes[i] = c;
    this.volumes[i] = v;
    this.length++;
  }

  // Calculate SMA without allocating intermediate arrays
  sma(period: number): Float64Array {
    const result = new Float64Array(this.length);
    let sum = 0;

    for (let i = 0; i < this.length; i++) {
      sum += this.closes[i];
      if (i >= period) {
        sum -= this.closes[i - period];
        result[i] = sum / period;
      } else if (i === period - 1) {
        result[i] = sum / period;
      }
    }

    return result;
  }
}
```

## SQLite Performance Optimization

### Batch Inserts with Transactions

```typescript
// Individual inserts: ~50 inserts/sec
// Batched transaction: ~100,000 inserts/sec
function batchInsertTicks(db: Database, ticks: TickData[]): void {
  const stmt = db.prepare(`
    INSERT INTO tick_log (symbol, price, volume, timestamp)
    VALUES (?, ?, ?, ?)
  `);

  const insertBatch = db.transaction((data: TickData[]) => {
    for (const tick of data) {
      stmt.run(tick.symbol, tick.lastPrice, tick.volume, tick.timestamp);
    }
  });

  insertBatch(ticks);
}

// Accumulate ticks and flush periodically
class TickLogger {
  private buffer: TickData[] = [];
  private flushInterval: Timer;

  constructor(private db: Database, flushEveryMs = 5000) {
    this.flushInterval = setInterval(() => this.flush(), flushEveryMs);
  }

  add(tick: TickData): void {
    this.buffer.push(tick);
    if (this.buffer.length >= 1000) this.flush();
  }

  private flush(): void {
    if (this.buffer.length === 0) return;
    const batch = this.buffer;
    this.buffer = [];
    batchInsertTicks(this.db, batch);
  }

  stop(): void {
    clearInterval(this.flushInterval);
    this.flush();
  }
}
```

### Query Optimization

```typescript
// ALWAYS use prepared statements (compiled once, executed many times)
// Bad:
db.query(`SELECT * FROM orders WHERE symbol = '${symbol}'`); // SQL injection + slow

// Good:
const stmt = db.prepare("SELECT * FROM orders WHERE symbol = ?");
stmt.all(symbol); // Safe + fast (compiled once)

// Use EXPLAIN to check query plans
const plan = db.query("EXPLAIN QUERY PLAN SELECT * FROM orders WHERE symbol = ? AND status = ?").all("INFY", "OPEN");
// Ensure it uses indexes
```

## Webview Rendering Performance

### Virtual Scrolling for Large Lists

```typescript
// Don't render 1000+ order rows — use virtual scrolling
class VirtualList {
  private container: HTMLElement;
  private itemHeight: number;
  private totalItems: number;
  private visibleCount: number;
  private startIndex = 0;
  private renderItem: (index: number) => HTMLElement;

  constructor(
    container: HTMLElement,
    itemHeight: number,
    totalItems: number,
    renderItem: (index: number) => HTMLElement
  ) {
    this.container = container;
    this.itemHeight = itemHeight;
    this.totalItems = totalItems;
    this.visibleCount = Math.ceil(container.clientHeight / itemHeight) + 2;
    this.renderItem = renderItem;

    // Set total height for scrollbar
    const spacer = document.createElement("div");
    spacer.style.height = `${totalItems * itemHeight}px`;
    container.appendChild(spacer);

    container.addEventListener("scroll", () => this.onScroll());
    this.render();
  }

  private onScroll(): void {
    const newStart = Math.floor(this.container.scrollTop / this.itemHeight);
    if (newStart !== this.startIndex) {
      this.startIndex = newStart;
      this.render();
    }
  }

  private render(): void {
    // Remove old items
    const existing = this.container.querySelectorAll(".virtual-item");
    existing.forEach((el) => el.remove());

    const end = Math.min(this.startIndex + this.visibleCount, this.totalItems);
    for (let i = this.startIndex; i < end; i++) {
      const el = this.renderItem(i);
      el.className = "virtual-item";
      el.style.position = "absolute";
      el.style.top = `${i * this.itemHeight}px`;
      el.style.height = `${this.itemHeight}px`;
      this.container.appendChild(el);
    }
  }

  updateTotal(total: number): void {
    this.totalItems = total;
    const spacer = this.container.firstElementChild as HTMLElement;
    spacer.style.height = `${total * this.itemHeight}px`;
    this.render();
  }
}
```

### Efficient DOM Updates

```typescript
// Batch DOM updates to avoid layout thrashing
function updatePositionTable(positions: Position[]): void {
  // Use DocumentFragment for batch insert
  const fragment = document.createDocumentFragment();

  for (const pos of positions) {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td>${pos.symbol}</td>
      <td class="${pos.quantity > 0 ? "long" : "short"}">${pos.quantity}</td>
      <td>${pos.averagePrice.toFixed(2)}</td>
      <td>${pos.lastPrice.toFixed(2)}</td>
      <td class="${pos.pnl >= 0 ? "profit" : "loss"}">${pos.pnl.toFixed(2)}</td>
    `;
    fragment.appendChild(row);
  }

  const tbody = document.getElementById("positions-body")!;
  tbody.innerHTML = "";
  tbody.appendChild(fragment);
}

// For frequent updates (tick prices), update only changed cells
function updatePriceCell(symbol: string, price: number, prevPrice: number): void {
  const cell = document.getElementById(`price-${symbol}`);
  if (!cell) return;

  // Only touch DOM if value actually changed
  const newText = price.toFixed(2);
  if (cell.textContent !== newText) {
    cell.textContent = newText;
    cell.className = price > prevPrice ? "tick-up" : price < prevPrice ? "tick-down" : "";
  }
}
```

## Memory Monitoring

```typescript
// Monitor memory usage and warn if it's getting high
function startMemoryMonitor(thresholdMB = 512): Timer {
  return setInterval(() => {
    const usage = process.memoryUsage();
    const heapMB = usage.heapUsed / 1024 / 1024;
    const rssMB = usage.rss / 1024 / 1024;

    if (heapMB > thresholdMB) {
      console.warn(`High memory usage: ${heapMB.toFixed(1)}MB heap, ${rssMB.toFixed(1)}MB RSS`);

      // Force GC if available (Bun supports this)
      if (typeof Bun !== "undefined" && Bun.gc) {
        Bun.gc(true);
        const after = process.memoryUsage().heapUsed / 1024 / 1024;
        console.log(`After GC: ${after.toFixed(1)}MB heap`);
      }
    }
  }, 30000); // Check every 30 seconds
}
```

## Startup Performance

```typescript
// Electrobun apps start in <50ms. Keep it fast:

// 1. Lazy-load non-critical services
let marketData: MarketDataService | null = null;

async function getMarketData(): Promise<MarketDataService> {
  if (!marketData) {
    marketData = new MarketDataService(broker);
    await marketData.connect();
  }
  return marketData;
}

// 2. Don't block window creation on data loading
const mainWindow = new BrowserWindow({
  title: "Trading",
  url: "views://mainview/index.html",
  frame: { x: 100, y: 100, width: 1400, height: 900 },
  rpc,
});

// Load data in background after window is shown
mainWindow.webview.on("dom-ready", async () => {
  const [positions, orders, margins] = await Promise.all([
    broker.getPositions(),
    broker.getOrders(),
    broker.getMargins(),
  ]);
  mainWindow.webview.rpc?.send.positionUpdate({ positions });
});

// 3. Check for updates non-blocking
setTimeout(() => checkUpdates(), 5000);
```

## Bundle Size Optimization

```typescript
// electrobun.config.ts — minimize view bundles
export default {
  build: {
    views: {
      mainview: {
        entrypoint: "src/mainview/index.ts",
        minify: true,       // Minify production builds
        external: [],       // Don't bundle external deps (use CDN or bundle separately)
      },
    },
    // Use ASAR for smaller distribution
    useAsar: true,
    asarUnpack: ["*.node", "*.dll", "*.dylib", "*.so"],
  },
};

// Electrobun native apps are ~14MB total (vs ~150MB+ for Electron)
// Delta updates can be as small as 14KB
```

## Performance Checklist

- [ ] All tick processing happens in Bun (not webview)
- [ ] UI updates throttled to max 5-10/sec per symbol
- [ ] SQLite using WAL mode and prepared statements
- [ ] Database writes batched in transactions
- [ ] Ring buffers used for bounded history (not growing arrays)
- [ ] Typed arrays used for numeric series data
- [ ] DOM updates batched (DocumentFragment or targeted cell updates)
- [ ] Virtual scrolling for tables with >100 rows
- [ ] Memory monitored and GC triggered if needed
- [ ] Non-critical services lazy-loaded after window appears
- [ ] Navigation rules restrict webview to prevent loading heavy external sites
- [ ] Old data cleaned up periodically (audit logs, OHLC cache, snapshots)
