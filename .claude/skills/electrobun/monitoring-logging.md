# Monitoring, Logging & Health Checks

Covers traffic logging, latency monitoring, health status, structured logging, and system metrics for Electrobun trading apps.

## Structured Logger

```typescript
// src/bun/services/logger.ts
import { Database } from "bun:sqlite";

type LogLevel = "debug" | "info" | "warn" | "error" | "fatal";

interface LogEntry {
  timestamp: number;
  level: LogLevel;
  context: string;  // e.g., "order-manager", "market-data", "broker:zerodha"
  message: string;
  meta?: Record<string, any>;
}

export class Logger {
  private db: Database;
  private buffer: LogEntry[] = [];
  private flushInterval: Timer;
  private minLevel: LogLevel;
  private onLog?: (entry: LogEntry) => void;

  private static LEVELS: Record<LogLevel, number> = {
    debug: 0, info: 1, warn: 2, error: 3, fatal: 4,
  };

  constructor(db: Database, opts?: { minLevel?: LogLevel; flushMs?: number; onLog?: (entry: LogEntry) => void }) {
    this.db = db;
    this.minLevel = opts?.minLevel ?? "info";
    this.onLog = opts?.onLog;

    db.run(`
      CREATE TABLE IF NOT EXISTS app_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp INTEGER NOT NULL,
        level TEXT NOT NULL,
        context TEXT NOT NULL,
        message TEXT NOT NULL,
        meta TEXT,
        created_at TEXT DEFAULT (datetime('now'))
      )
    `);
    db.run("CREATE INDEX IF NOT EXISTS idx_logs_ts ON app_logs(timestamp)");
    db.run("CREATE INDEX IF NOT EXISTS idx_logs_level ON app_logs(level)");
    db.run("CREATE INDEX IF NOT EXISTS idx_logs_ctx ON app_logs(context)");

    this.flushInterval = setInterval(() => this.flush(), opts?.flushMs ?? 2000);
  }

  private shouldLog(level: LogLevel): boolean {
    return Logger.LEVELS[level] >= Logger.LEVELS[this.minLevel];
  }

  log(level: LogLevel, context: string, message: string, meta?: Record<string, any>): void {
    if (!this.shouldLog(level)) return;

    const entry: LogEntry = { timestamp: Date.now(), level, context, message, meta };
    this.buffer.push(entry);

    // Always print errors immediately
    if (level === "error" || level === "fatal") {
      console.error(`[${level.toUpperCase()}] [${context}] ${message}`, meta ?? "");
    }

    // Forward to webview callback
    this.onLog?.(entry);

    // Auto-flush if buffer is large
    if (this.buffer.length >= 100) this.flush();
  }

  debug(ctx: string, msg: string, meta?: Record<string, any>) { this.log("debug", ctx, msg, meta); }
  info(ctx: string, msg: string, meta?: Record<string, any>) { this.log("info", ctx, msg, meta); }
  warn(ctx: string, msg: string, meta?: Record<string, any>) { this.log("warn", ctx, msg, meta); }
  error(ctx: string, msg: string, meta?: Record<string, any>) { this.log("error", ctx, msg, meta); }
  fatal(ctx: string, msg: string, meta?: Record<string, any>) { this.log("fatal", ctx, msg, meta); }

  private flush(): void {
    if (this.buffer.length === 0) return;

    const insert = this.db.prepare(
      "INSERT INTO app_logs (timestamp, level, context, message, meta) VALUES (?, ?, ?, ?, ?)"
    );

    const batch = this.db.transaction((entries: LogEntry[]) => {
      for (const e of entries) {
        insert.run(e.timestamp, e.level, e.context, e.message, e.meta ? JSON.stringify(e.meta) : null);
      }
    });

    batch(this.buffer);
    this.buffer = [];
  }

  query(opts: { level?: LogLevel; context?: string; from?: number; to?: number; limit?: number }): LogEntry[] {
    const conditions: string[] = [];
    const params: any[] = [];

    if (opts.level) { conditions.push("level = ?"); params.push(opts.level); }
    if (opts.context) { conditions.push("context = ?"); params.push(opts.context); }
    if (opts.from) { conditions.push("timestamp >= ?"); params.push(opts.from); }
    if (opts.to) { conditions.push("timestamp <= ?"); params.push(opts.to); }

    const where = conditions.length > 0 ? `WHERE ${conditions.join(" AND ")}` : "";
    const limit = opts.limit ?? 100;

    return this.db.query(
      `SELECT timestamp, level, context, message, meta FROM app_logs ${where} ORDER BY timestamp DESC LIMIT ?`
    ).all(...params, limit) as LogEntry[];
  }

  prune(olderThanDays: number): number {
    const cutoff = Date.now() - olderThanDays * 86400000;
    const result = this.db.run("DELETE FROM app_logs WHERE timestamp < ?", [cutoff]);
    return result.changes;
  }

  destroy(): void {
    clearInterval(this.flushInterval);
    this.flush();
  }
}
```

## Traffic Logger (API Request/Response Tracking)

```typescript
// src/bun/services/traffic-logger.ts
import { Database } from "bun:sqlite";

interface TrafficEntry {
  id?: number;
  timestamp: number;
  direction: "outbound" | "inbound";  // outbound = to broker, inbound = from broker
  broker: string;
  endpoint: string;
  method: string;
  status: number;
  latencyMs: number;
  requestSize: number;
  responseSize: number;
  error?: string;
}

export class TrafficLogger {
  private db: Database;
  private insertStmt: ReturnType<Database["prepare"]>;

  constructor(db: Database) {
    this.db = db;

    db.run(`
      CREATE TABLE IF NOT EXISTS traffic_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp INTEGER NOT NULL,
        direction TEXT NOT NULL,
        broker TEXT NOT NULL,
        endpoint TEXT NOT NULL,
        method TEXT NOT NULL,
        status INTEGER NOT NULL,
        latency_ms REAL NOT NULL,
        request_size INTEGER DEFAULT 0,
        response_size INTEGER DEFAULT 0,
        error TEXT,
        created_at TEXT DEFAULT (datetime('now'))
      )
    `);
    db.run("CREATE INDEX IF NOT EXISTS idx_traffic_ts ON traffic_log(timestamp)");
    db.run("CREATE INDEX IF NOT EXISTS idx_traffic_broker ON traffic_log(broker)");

    this.insertStmt = db.prepare(`
      INSERT INTO traffic_log (timestamp, direction, broker, endpoint, method, status, latency_ms, request_size, response_size, error)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `);
  }

  log(entry: TrafficEntry): void {
    this.insertStmt.run(
      entry.timestamp, entry.direction, entry.broker, entry.endpoint,
      entry.method, entry.status, entry.latencyMs,
      entry.requestSize, entry.responseSize, entry.error ?? null
    );
  }

  // Instrumented fetch wrapper
  createInstrumentedFetch(broker: string) {
    return async (url: string, init?: RequestInit): Promise<Response> => {
      const start = performance.now();
      const method = init?.method ?? "GET";
      const requestSize = init?.body ? new Blob([init.body]).size : 0;
      let status = 0;
      let responseSize = 0;
      let error: string | undefined;

      try {
        const response = await fetch(url, init);
        status = response.status;
        // Clone to read size without consuming body
        const clone = response.clone();
        const body = await clone.arrayBuffer();
        responseSize = body.byteLength;
        return response;
      } catch (e: any) {
        status = 0;
        error = e.message;
        throw e;
      } finally {
        const latencyMs = performance.now() - start;
        const endpoint = new URL(url).pathname;
        this.log({
          timestamp: Date.now(), direction: "outbound", broker, endpoint,
          method, status, latencyMs, requestSize, responseSize, error,
        });
      }
    };
  }

  getRecent(limit = 50, offset = 0): TrafficEntry[] {
    return this.db.query(
      "SELECT * FROM traffic_log ORDER BY timestamp DESC LIMIT ? OFFSET ?"
    ).all(limit, offset) as TrafficEntry[];
  }

  getByBroker(broker: string, limit = 50): TrafficEntry[] {
    return this.db.query(
      "SELECT * FROM traffic_log WHERE broker = ? ORDER BY timestamp DESC LIMIT ?"
    ).all(broker, limit) as TrafficEntry[];
  }

  getErrors(limit = 50): TrafficEntry[] {
    return this.db.query(
      "SELECT * FROM traffic_log WHERE status = 0 OR status >= 400 ORDER BY timestamp DESC LIMIT ?"
    ).all(limit) as TrafficEntry[];
  }

  prune(olderThanDays: number): number {
    const cutoff = Date.now() - olderThanDays * 86400000;
    return this.db.run("DELETE FROM traffic_log WHERE timestamp < ?", [cutoff]).changes;
  }
}
```

## Latency Monitor

```typescript
// src/bun/services/latency-monitor.ts

interface LatencyBucket {
  endpoint: string;
  count: number;
  totalMs: number;
  minMs: number;
  maxMs: number;
  p50Ms: number;
  p95Ms: number;
  p99Ms: number;
  errorCount: number;
}

export class LatencyMonitor {
  private buckets = new Map<string, number[]>();
  private errors = new Map<string, number>();
  private windowMs: number;
  private lastReset: number;

  constructor(windowMs = 60000) {
    this.windowMs = windowMs;
    this.lastReset = Date.now();
  }

  record(endpoint: string, latencyMs: number, isError = false): void {
    this.maybeReset();

    if (!this.buckets.has(endpoint)) this.buckets.set(endpoint, []);
    this.buckets.get(endpoint)!.push(latencyMs);

    if (isError) {
      this.errors.set(endpoint, (this.errors.get(endpoint) ?? 0) + 1);
    }
  }

  private maybeReset(): void {
    if (Date.now() - this.lastReset > this.windowMs) {
      this.buckets.clear();
      this.errors.clear();
      this.lastReset = Date.now();
    }
  }

  private percentile(sorted: number[], p: number): number {
    if (sorted.length === 0) return 0;
    const idx = Math.ceil((p / 100) * sorted.length) - 1;
    return sorted[Math.max(0, idx)];
  }

  getStats(): LatencyBucket[] {
    const stats: LatencyBucket[] = [];

    for (const [endpoint, latencies] of this.buckets) {
      const sorted = [...latencies].sort((a, b) => a - b);
      stats.push({
        endpoint,
        count: sorted.length,
        totalMs: sorted.reduce((a, b) => a + b, 0),
        minMs: sorted[0] ?? 0,
        maxMs: sorted[sorted.length - 1] ?? 0,
        p50Ms: this.percentile(sorted, 50),
        p95Ms: this.percentile(sorted, 95),
        p99Ms: this.percentile(sorted, 99),
        errorCount: this.errors.get(endpoint) ?? 0,
      });
    }

    return stats.sort((a, b) => b.count - a.count);
  }

  getSummary(): { totalRequests: number; avgLatencyMs: number; errorRate: number } {
    let totalRequests = 0;
    let totalLatency = 0;
    let totalErrors = 0;

    for (const [, latencies] of this.buckets) {
      totalRequests += latencies.length;
      totalLatency += latencies.reduce((a, b) => a + b, 0);
    }
    for (const [, count] of this.errors) {
      totalErrors += count;
    }

    return {
      totalRequests,
      avgLatencyMs: totalRequests > 0 ? totalLatency / totalRequests : 0,
      errorRate: totalRequests > 0 ? totalErrors / totalRequests : 0,
    };
  }
}
```

## Health Check Service

```typescript
// src/bun/services/health.ts

interface HealthStatus {
  overall: "healthy" | "degraded" | "unhealthy";
  uptime: number;
  memory: { heapUsed: number; heapTotal: number; rss: number };
  components: ComponentHealth[];
  timestamp: number;
}

interface ComponentHealth {
  name: string;
  status: "up" | "down" | "degraded";
  latencyMs?: number;
  message?: string;
  lastCheck: number;
}

export class HealthService {
  private startTime = Date.now();
  private checks = new Map<string, () => Promise<ComponentHealth>>();

  registerCheck(name: string, check: () => Promise<ComponentHealth>): void {
    this.checks.set(name, check);
  }

  async getStatus(): Promise<HealthStatus> {
    const components: ComponentHealth[] = [];

    for (const [name, check] of this.checks) {
      try {
        const result = await check();
        components.push(result);
      } catch (e: any) {
        components.push({ name, status: "down", message: e.message, lastCheck: Date.now() });
      }
    }

    const hasDown = components.some((c) => c.status === "down");
    const hasDegraded = components.some((c) => c.status === "degraded");

    return {
      overall: hasDown ? "unhealthy" : hasDegraded ? "degraded" : "healthy",
      uptime: Date.now() - this.startTime,
      memory: process.memoryUsage(),
      components,
      timestamp: Date.now(),
    };
  }
}

// Register health checks for trading app components
export function setupHealthChecks(
  health: HealthService,
  broker: { isConnected(): boolean },
  wsAdapter: { isConnected(): boolean },
  db: { query(sql: string): any }
): void {
  // Broker API connectivity
  health.registerCheck("broker-api", async () => ({
    name: "broker-api",
    status: broker.isConnected() ? "up" : "down",
    message: broker.isConnected() ? "Connected" : "Disconnected",
    lastCheck: Date.now(),
  }));

  // WebSocket feed
  health.registerCheck("market-feed", async () => ({
    name: "market-feed",
    status: wsAdapter.isConnected() ? "up" : "down",
    message: wsAdapter.isConnected() ? "Streaming" : "Disconnected",
    lastCheck: Date.now(),
  }));

  // Database
  health.registerCheck("database", async () => {
    const start = performance.now();
    try {
      db.query("SELECT 1").get();
      return {
        name: "database",
        status: "up",
        latencyMs: performance.now() - start,
        lastCheck: Date.now(),
      };
    } catch (e: any) {
      return { name: "database", status: "down", message: e.message, lastCheck: Date.now() };
    }
  });

  // Memory pressure
  health.registerCheck("memory", async () => {
    const mem = process.memoryUsage();
    const heapPercent = mem.heapUsed / mem.heapTotal;
    return {
      name: "memory",
      status: heapPercent > 0.9 ? "degraded" : "up",
      message: `${(heapPercent * 100).toFixed(1)}% heap used (${(mem.heapUsed / 1024 / 1024).toFixed(1)}MB)`,
      lastCheck: Date.now(),
    };
  });
}
```

## System Metrics Collector

```typescript
// src/bun/services/metrics.ts

interface SystemMetrics {
  timestamp: number;
  cpu: { user: number; system: number };
  memory: { heapUsed: number; heapTotal: number; rss: number; external: number };
  ticks: { perSecond: number; totalToday: number };
  orders: { placedToday: number; filledToday: number; rejectedToday: number };
  websocket: { connected: boolean; reconnectCount: number; lastMessageAge: number };
}

export class MetricsCollector {
  private tickCount = 0;
  private ticksPerSecond = 0;
  private orderStats = { placed: 0, filled: 0, rejected: 0 };
  private wsReconnects = 0;
  private lastTickTime = 0;
  private tickWindow: number[] = [];

  recordTick(): void {
    this.tickCount++;
    const now = Date.now();
    this.tickWindow.push(now);
    // Keep only last 5 seconds
    const cutoff = now - 5000;
    while (this.tickWindow.length > 0 && this.tickWindow[0] < cutoff) {
      this.tickWindow.shift();
    }
    this.ticksPerSecond = this.tickWindow.length / 5;
    this.lastTickTime = now;
  }

  recordOrder(status: "placed" | "filled" | "rejected"): void {
    this.orderStats[status]++;
  }

  recordReconnect(): void {
    this.wsReconnects++;
  }

  getMetrics(wsConnected: boolean): SystemMetrics {
    const mem = process.memoryUsage();
    const cpu = process.cpuUsage();

    return {
      timestamp: Date.now(),
      cpu: { user: cpu.user / 1000, system: cpu.system / 1000 }, // μs to ms
      memory: {
        heapUsed: mem.heapUsed,
        heapTotal: mem.heapTotal,
        rss: mem.rss,
        external: mem.external,
      },
      ticks: { perSecond: Math.round(this.ticksPerSecond), totalToday: this.tickCount },
      orders: { ...this.orderStats },
      websocket: {
        connected: wsConnected,
        reconnectCount: this.wsReconnects,
        lastMessageAge: this.lastTickTime > 0 ? Date.now() - this.lastTickTime : -1,
      },
    };
  }

  resetDaily(): void {
    this.tickCount = 0;
    this.orderStats = { placed: 0, filled: 0, rejected: 0 };
    this.wsReconnects = 0;
  }
}
```

## Wiring Monitoring to RPC

```typescript
// In src/bun/index.ts — RPC handlers for monitoring
const logger = new Logger(db, {
  minLevel: "info",
  onLog: (entry) => {
    // Forward logs to webview in real-time
    mainWindow.webview.rpc?.send.logMessage({
      level: entry.level,
      message: `[${entry.context}] ${entry.message}`,
      context: entry.context,
    });
  },
});

const trafficLogger = new TrafficLogger(db);
const latencyMonitor = new LatencyMonitor(60000);
const health = new HealthService();
const metrics = new MetricsCollector();

// RPC request handlers
getHealthStatus: async () => {
  return health.getStatus();
},

getLatencyStats: async () => {
  return {
    stats: latencyMonitor.getStats(),
    summary: latencyMonitor.getSummary(),
  };
},

getTrafficLogs: async ({ page, limit }) => {
  return trafficLogger.getRecent(limit, (page - 1) * limit);
},

getSystemMetrics: async () => {
  return metrics.getMetrics(wsAdapter.isConnected());
},

getLogs: async ({ level, context, limit }) => {
  return logger.query({ level, context, limit });
},

// Push periodic metrics to UI
setInterval(() => {
  const m = metrics.getMetrics(wsAdapter.isConnected());
  mainWindow.webview.rpc?.send.metricsUpdate(m);
}, 5000);

// Daily maintenance
function scheduleDailyMaintenance() {
  const now = new Date();
  const nextMidnight = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1, 0, 0, 0);
  const msUntilMidnight = nextMidnight.getTime() - now.getTime();

  setTimeout(() => {
    logger.prune(30);          // Keep 30 days of logs
    trafficLogger.prune(7);    // Keep 7 days of traffic
    metrics.resetDaily();
    scheduleDailyMaintenance(); // Reschedule
  }, msUntilMidnight);
}
scheduleDailyMaintenance();
```

## RPC Schema Additions

```typescript
// Add to your RPC schema for monitoring
type MonitoringRPC = {
  bun: RPCSchema<{
    requests: {
      getHealthStatus: { params: {}; response: HealthStatus };
      getLatencyStats: { params: {}; response: { stats: LatencyBucket[]; summary: { totalRequests: number; avgLatencyMs: number; errorRate: number } } };
      getTrafficLogs: { params: { page: number; limit: number }; response: TrafficEntry[] };
      getSystemMetrics: { params: {}; response: SystemMetrics };
      getLogs: { params: { level?: string; context?: string; limit?: number }; response: LogEntry[] };
    };
  }>;
  webview: RPCSchema<{
    messages: {
      metricsUpdate: SystemMetrics;
      logMessage: { level: string; message: string; context?: string };
    };
  }>;
};
```
