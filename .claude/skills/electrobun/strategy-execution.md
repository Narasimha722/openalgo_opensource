# Strategy Execution Patterns for Electrobun

Covers Python strategy subprocess execution, scheduling, visual flow workflows, webhook strategies, and the action center for semi-automatic order approval.

## Python Strategy Execution

OpenAlgo runs Python strategies as isolated subprocesses. In Electrobun, use `Bun.spawn()`.

```typescript
// src/bun/services/strategy-runner.ts
import { Utils } from "electrobun/bun";

interface StrategyProcess {
  id: string;
  name: string;
  proc: Subprocess;
  pid: number;
  status: "running" | "stopped" | "error";
  startedAt: number;
  logFile: string;
}

export class StrategyRunner {
  private processes = new Map<string, StrategyProcess>();
  private strategiesDir: string;
  private logsDir: string;

  constructor() {
    this.strategiesDir = `${Utils.paths.userData}/strategies`;
    this.logsDir = `${Utils.paths.userData}/logs/strategies`;
    // Ensure directories exist
    Bun.spawnSync(["mkdir", "-p", this.strategiesDir, this.logsDir]);
  }

  async start(
    strategyId: string,
    onLog: (line: string, stream: "stdout" | "stderr") => void
  ): Promise<{ pid: number }> {
    if (this.processes.has(strategyId)) {
      throw new Error(`Strategy ${strategyId} is already running`);
    }

    const scriptPath = `${this.strategiesDir}/${strategyId}.py`;
    const logPath = `${this.logsDir}/${strategyId}.log`;

    // Check script exists
    const file = Bun.file(scriptPath);
    if (!await file.exists()) {
      throw new Error(`Strategy script not found: ${scriptPath}`);
    }

    const proc = Bun.spawn(["python3", scriptPath], {
      cwd: this.strategiesDir,
      env: {
        ...process.env,
        OPENALGO_API_URL: "http://localhost:5000", // Local API server
        STRATEGY_ID: strategyId,
      },
      stdout: "pipe",
      stderr: "pipe",
    });

    const entry: StrategyProcess = {
      id: strategyId,
      name: strategyId,
      proc,
      pid: proc.pid,
      status: "running",
      startedAt: Date.now(),
      logFile: logPath,
    };

    this.processes.set(strategyId, entry);

    // Stream stdout
    this.streamOutput(proc.stdout, strategyId, "stdout", onLog, logPath);
    this.streamOutput(proc.stderr, strategyId, "stderr", onLog, logPath);

    // Watch for exit
    proc.exited.then((exitCode) => {
      const p = this.processes.get(strategyId);
      if (p) {
        p.status = exitCode === 0 ? "stopped" : "error";
        onLog(`Process exited with code ${exitCode}`, "stdout");
      }
    });

    return { pid: proc.pid };
  }

  stop(strategyId: string): boolean {
    const entry = this.processes.get(strategyId);
    if (!entry || entry.status !== "running") return false;

    entry.proc.kill("SIGTERM");

    // Force kill after 5 seconds
    setTimeout(() => {
      if (entry.status === "running") {
        entry.proc.kill("SIGKILL");
      }
    }, 5000);

    entry.status = "stopped";
    return true;
  }

  stopAll(): void {
    for (const [id] of this.processes) {
      this.stop(id);
    }
  }

  getStatus(strategyId: string): StrategyProcess | undefined {
    return this.processes.get(strategyId);
  }

  getAllStatuses(): StrategyProcess[] {
    return Array.from(this.processes.values());
  }

  private async streamOutput(
    stream: ReadableStream<Uint8Array>,
    strategyId: string,
    type: "stdout" | "stderr",
    onLog: (line: string, stream: "stdout" | "stderr") => void,
    logPath: string
  ): Promise<void> {
    const reader = stream.getReader();
    const decoder = new TextDecoder();
    const logFile = Bun.file(logPath);
    const writer = logFile.writer();

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        const text = decoder.decode(value);
        const lines = text.split("\n").filter(Boolean);

        for (const line of lines) {
          const timestamp = new Date().toISOString();
          const logLine = `[${timestamp}] [${type}] ${line}`;

          // Write to log file
          writer.write(logLine + "\n");

          // Push to callback (RPC message to webview)
          onLog(line, type);
        }
      }
    } catch (e) {
      // Stream closed
    } finally {
      writer.end();
    }
  }
}
```

## Strategy Scheduler

```typescript
// src/bun/services/strategy-scheduler.ts
export class StrategyScheduler {
  private schedules = new Map<string, { timer: Timer; config: ScheduleConfig }>();
  private runner: StrategyRunner;
  private marketCalendar: MarketCalendar;

  constructor(runner: StrategyRunner, calendar: MarketCalendar) {
    this.runner = runner;
    this.marketCalendar = calendar;
  }

  schedule(strategyId: string, config: ScheduleConfig): void {
    this.unschedule(strategyId);

    // Check every minute
    const timer = setInterval(() => {
      this.checkAndRun(strategyId, config);
    }, 60000);

    this.schedules.set(strategyId, { timer, config });
  }

  unschedule(strategyId: string): void {
    const entry = this.schedules.get(strategyId);
    if (entry) {
      clearInterval(entry.timer);
      this.schedules.delete(strategyId);
    }
  }

  private async checkAndRun(strategyId: string, config: ScheduleConfig): Promise<void> {
    const now = new Date();
    const day = now.toLocaleDateString("en-US", { weekday: "long" }).toLowerCase();
    const time = `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;

    // Check if today is a trading day
    if (!this.marketCalendar.isTradingDay(now)) return;

    // Check if current day matches schedule
    if (!config.days.includes(day)) return;

    // Check start time
    if (time === config.startTime) {
      const status = this.runner.getStatus(strategyId);
      if (!status || status.status !== "running") {
        await this.runner.start(strategyId, () => {});
      }
    }

    // Check stop time
    if (time === config.stopTime) {
      this.runner.stop(strategyId);
    }
  }
}

interface ScheduleConfig {
  days: string[];          // ["monday", "tuesday", ...]
  startTime: string;       // "09:15"
  stopTime: string;        // "15:30"
  autoRestart: boolean;
}
```

## Flow Workflow Engine

OpenAlgo has a visual node-based workflow editor with 70+ node types.

```typescript
// src/bun/services/flow-engine.ts

interface FlowNode {
  id: string;
  type: string;
  data: Record<string, any>;
  position: { x: number; y: number };
}

interface FlowEdge {
  id: string;
  source: string;
  target: string;
  sourceHandle?: string;
  targetHandle?: string;
}

interface FlowExecution {
  flowId: string;
  nodeOutputs: Map<string, any>;
  logs: { nodeId: string; message: string; timestamp: number }[];
  status: "running" | "completed" | "error";
}

export class FlowEngine {
  private nodeHandlers = new Map<string, NodeHandler>();

  constructor(
    private broker: BrokerPlugin,
    private tokens: AuthTokens,
    private onLog: (flowId: string, nodeId: string, message: string) => void
  ) {
    this.registerBuiltinNodes();
  }

  private registerBuiltinNodes(): void {
    // Data nodes
    this.registerNode("symbol", async (data) => ({
      symbol: data.symbol,
      exchange: data.exchange,
    }));

    this.registerNode("getQuote", async (data, inputs) => {
      const { symbol, exchange } = inputs.symbol || data;
      return this.broker.getQuote(symbol, exchange, this.tokens);
    });

    this.registerNode("getDepth", async (data, inputs) => {
      const { symbol, exchange } = inputs.symbol || data;
      return this.broker.getDepth(symbol, exchange, this.tokens);
    });

    // Condition nodes
    this.registerNode("priceCondition", async (data, inputs) => {
      const quote = inputs.quote;
      const { operator, value, field } = data;
      const price = quote[field || "lastPrice"];
      return this.evaluateCondition(price, operator, value);
    });

    this.registerNode("timeCondition", async (data) => {
      const now = new Date();
      const currentTime = `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
      return currentTime >= data.startTime && currentTime <= data.endTime;
    });

    // Order nodes
    this.registerNode("placeOrder", async (data, inputs) => {
      const params = { ...data, ...inputs };
      return this.broker.placeOrder(params, this.tokens);
    });

    this.registerNode("cancelOrder", async (data, inputs) => {
      const orderId = inputs.orderId || data.orderId;
      return this.broker.cancelOrder(orderId, "regular", this.tokens);
    });

    // Action nodes
    this.registerNode("delay", async (data) => {
      await Bun.sleep(data.milliseconds || 1000);
      return true;
    });

    this.registerNode("log", async (data, inputs) => {
      const message = data.template
        ? data.template.replace(/\{(\w+)\}/g, (_: any, key: string) => String(inputs[key] ?? ""))
        : JSON.stringify(inputs);
      return message;
    });

    this.registerNode("telegramAlert", async (data, inputs) => {
      const message = typeof inputs === "string" ? inputs : JSON.stringify(inputs);
      await sendTelegramMessage(data.botToken, data.chatId, message);
      return true;
    });

    this.registerNode("httpRequest", async (data) => {
      const response = await fetch(data.url, {
        method: data.method || "GET",
        headers: data.headers,
        body: data.body ? JSON.stringify(data.body) : undefined,
      });
      return response.json();
    });

    // Logic gates
    this.registerNode("andGate", async (_data, inputs) => {
      return Object.values(inputs).every(Boolean);
    });

    this.registerNode("orGate", async (_data, inputs) => {
      return Object.values(inputs).some(Boolean);
    });

    this.registerNode("notGate", async (_data, inputs) => {
      return !Object.values(inputs)[0];
    });
  }

  registerNode(type: string, handler: NodeHandler): void {
    this.nodeHandlers.set(type, handler);
  }

  async execute(flowId: string, nodes: FlowNode[], edges: FlowEdge[]): Promise<FlowExecution> {
    const execution: FlowExecution = {
      flowId,
      nodeOutputs: new Map(),
      logs: [],
      status: "running",
    };

    try {
      // Build adjacency list
      const adjacency = new Map<string, string[]>();
      const inDegree = new Map<string, number>();
      const edgeMap = new Map<string, FlowEdge[]>();

      for (const node of nodes) {
        adjacency.set(node.id, []);
        inDegree.set(node.id, 0);
        edgeMap.set(node.id, []);
      }

      for (const edge of edges) {
        adjacency.get(edge.source)!.push(edge.target);
        inDegree.set(edge.target, (inDegree.get(edge.target) || 0) + 1);
        edgeMap.get(edge.target)!.push(edge);
      }

      // Topological sort (BFS)
      const queue: string[] = [];
      for (const [nodeId, degree] of inDegree) {
        if (degree === 0) queue.push(nodeId);
      }

      while (queue.length > 0) {
        const nodeId = queue.shift()!;
        const node = nodes.find((n) => n.id === nodeId)!;

        // Gather inputs from upstream nodes
        const inputs: Record<string, any> = {};
        for (const edge of edgeMap.get(nodeId) || []) {
          const key = edge.sourceHandle || "default";
          inputs[key] = execution.nodeOutputs.get(edge.source);
        }

        // Execute node
        const handler = this.nodeHandlers.get(node.type);
        if (!handler) {
          execution.logs.push({ nodeId, message: `Unknown node type: ${node.type}`, timestamp: Date.now() });
          continue;
        }

        try {
          const output = await handler(node.data, inputs);
          execution.nodeOutputs.set(nodeId, output);
          execution.logs.push({ nodeId, message: `Executed: ${JSON.stringify(output).slice(0, 200)}`, timestamp: Date.now() });
          this.onLog(flowId, nodeId, `OK: ${typeof output}`);
        } catch (e) {
          const msg = e instanceof Error ? e.message : String(e);
          execution.logs.push({ nodeId, message: `Error: ${msg}`, timestamp: Date.now() });
          this.onLog(flowId, nodeId, `Error: ${msg}`);
          // Continue execution (don't stop on individual node failure)
        }

        // Advance to downstream nodes
        for (const next of adjacency.get(nodeId) || []) {
          inDegree.set(next, (inDegree.get(next) || 0) - 1);
          if (inDegree.get(next) === 0) {
            queue.push(next);
          }
        }
      }

      execution.status = "completed";
    } catch (e) {
      execution.status = "error";
    }

    return execution;
  }

  private evaluateCondition(value: number, operator: string, target: number): boolean {
    switch (operator) {
      case ">": return value > target;
      case "<": return value < target;
      case ">=": return value >= target;
      case "<=": return value <= target;
      case "==": return value === target;
      case "!=": return value !== target;
      default: return false;
    }
  }
}

type NodeHandler = (data: Record<string, any>, inputs: Record<string, any>) => Promise<any>;
```

## Webhook Strategy System

```typescript
// src/bun/services/webhook-handler.ts
// Exposed via local HTTP API server for external tools (TradingView, Chartink, etc.)

export class WebhookHandler {
  private strategies = new Map<string, WebhookStrategy>();

  registerStrategy(strategy: WebhookStrategy): void {
    this.strategies.set(strategy.id, strategy);
  }

  async handleWebhook(strategyId: string, payload: any, authToken: string): Promise<any> {
    const strategy = this.strategies.get(strategyId);
    if (!strategy) throw new Error(`Strategy ${strategyId} not found`);

    // Verify webhook token
    if (strategy.webhookToken !== authToken) {
      throw new Error("Invalid webhook token");
    }

    // Map payload to order params using strategy config
    const orderParams = this.mapPayloadToOrder(payload, strategy.config);

    // Place order via order router
    return this.orderRouter.placeOrder(orderParams);
  }

  private mapPayloadToOrder(payload: any, config: StrategyConfig): OrderParams {
    return {
      symbol: payload.symbol || config.defaultSymbol,
      exchange: payload.exchange || config.defaultExchange,
      transactionType: payload.action?.toUpperCase() || "BUY",
      quantity: payload.quantity || config.defaultQuantity,
      orderType: payload.orderType || "MARKET",
      product: payload.product || config.defaultProduct,
      price: payload.price,
      triggerPrice: payload.triggerPrice,
    };
  }
}

interface WebhookStrategy {
  id: string;
  name: string;
  webhookToken: string;
  config: StrategyConfig;
}
```

## Action Center (Semi-Auto Order Approval)

```typescript
// src/bun/services/action-center.ts
import { Database } from "bun:sqlite";

export class ActionCenter {
  private db: Database;

  constructor(db: Database) {
    this.db = db;
    db.run(`
      CREATE TABLE IF NOT EXISTS pending_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        order_data TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at INTEGER NOT NULL,
        updated_at INTEGER
      )
    `);
  }

  createPending(userId: string, orderData: OrderParams): number {
    const result = this.db.run(
      "INSERT INTO pending_orders (user_id, order_data, status, created_at) VALUES (?, ?, 'pending', ?)",
      [userId, JSON.stringify(orderData), Date.now()]
    );
    return Number(result.lastInsertRowid);
  }

  getPending(): PendingOrder[] {
    return this.db.query("SELECT * FROM pending_orders WHERE status = 'pending' ORDER BY created_at DESC").all() as any[];
  }

  async approve(id: number, orderRouter: OrderRouter): Promise<OrderResult> {
    const row = this.db.query("SELECT * FROM pending_orders WHERE id = ?").get(id) as any;
    if (!row || row.status !== "pending") throw new Error("Order not found or already processed");

    const orderData = JSON.parse(row.order_data);
    const result = await orderRouter.placeOrder(orderData);

    this.db.run("UPDATE pending_orders SET status = 'approved', updated_at = ? WHERE id = ?", [Date.now(), id]);
    return result;
  }

  reject(id: number): void {
    this.db.run("UPDATE pending_orders SET status = 'rejected', updated_at = ? WHERE id = ?", [Date.now(), id]);
  }
}
```

## Telegram Bot Integration

```typescript
// src/bun/services/telegram.ts
const TELEGRAM_API = "https://api.telegram.org/bot";

export class TelegramBot {
  private botToken: string;
  private chatId: string;
  private pollingTimer: Timer | null = null;
  private lastUpdateId = 0;

  constructor(botToken: string, chatId: string) {
    this.botToken = botToken;
    this.chatId = chatId;
  }

  async sendMessage(text: string, parseMode: "HTML" | "Markdown" = "HTML"): Promise<void> {
    await fetch(`${TELEGRAM_API}${this.botToken}/sendMessage`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        chat_id: this.chatId,
        text,
        parse_mode: parseMode,
      }),
    });
  }

  // Send order notification
  async notifyOrder(order: Order): Promise<void> {
    const emoji = order.transactionType === "BUY" ? "🟢" : "🔴";
    const message = [
      `${emoji} <b>${order.transactionType} ${order.symbol}</b>`,
      `Qty: ${order.quantity} | Price: ${order.price}`,
      `Type: ${order.orderType} | Product: ${order.product}`,
      `Status: ${order.status}`,
    ].join("\n");

    await this.sendMessage(message);
  }

  // Send P&L summary
  async notifyPnL(summary: { realized: number; unrealized: number; positions: number }): Promise<void> {
    const emoji = summary.realized + summary.unrealized >= 0 ? "📈" : "📉";
    const message = [
      `${emoji} <b>P&L Summary</b>`,
      `Realized: ₹${summary.realized.toFixed(2)}`,
      `Unrealized: ₹${summary.unrealized.toFixed(2)}`,
      `Open Positions: ${summary.positions}`,
    ].join("\n");

    await this.sendMessage(message);
  }

  // Poll for commands from Telegram (optional)
  startPolling(onCommand: (command: string, args: string) => void): void {
    this.pollingTimer = setInterval(async () => {
      try {
        const response = await fetch(
          `${TELEGRAM_API}${this.botToken}/getUpdates?offset=${this.lastUpdateId + 1}&timeout=1`
        );
        const data = await response.json();

        for (const update of data.result || []) {
          this.lastUpdateId = update.update_id;
          const text = update.message?.text;
          if (text?.startsWith("/")) {
            const [command, ...args] = text.split(" ");
            onCommand(command.slice(1), args.join(" "));
          }
        }
      } catch (e) {
        // Ignore polling errors
      }
    }, 5000);
  }

  stopPolling(): void {
    if (this.pollingTimer) {
      clearInterval(this.pollingTimer);
      this.pollingTimer = null;
    }
  }
}
```

## Market Calendar

```typescript
// src/bun/services/market-calendar.ts
import { Database } from "bun:sqlite";

export class MarketCalendar {
  private db: Database;
  private holidayCache = new Set<string>();

  constructor(db: Database) {
    this.db = db;
    db.run(`
      CREATE TABLE IF NOT EXISTS market_holidays (
        date TEXT PRIMARY KEY,
        exchange TEXT NOT NULL,
        description TEXT
      )
    `);
    db.run(`
      CREATE TABLE IF NOT EXISTS market_timings (
        exchange TEXT PRIMARY KEY,
        open_time TEXT NOT NULL,
        close_time TEXT NOT NULL,
        pre_open_time TEXT,
        post_close_time TEXT
      )
    `);
    this.loadHolidayCache();
  }

  private loadHolidayCache(): void {
    const holidays = this.db.query("SELECT date FROM market_holidays").all() as any[];
    for (const h of holidays) this.holidayCache.add(h.date);
  }

  isTradingDay(date: Date = new Date()): boolean {
    const day = date.getDay();
    if (day === 0 || day === 6) return false; // Weekend
    const dateStr = date.toISOString().split("T")[0];
    return !this.holidayCache.has(dateStr);
  }

  isMarketOpen(exchange = "NSE"): boolean {
    if (!this.isTradingDay()) return false;

    const timing = this.db.query("SELECT * FROM market_timings WHERE exchange = ?").get(exchange) as any;
    if (!timing) return false;

    const now = new Date();
    const currentTime = `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
    return currentTime >= timing.open_time && currentTime <= timing.close_time;
  }

  getTimings(exchange: string): { openTime: string; closeTime: string } | null {
    const row = this.db.query("SELECT * FROM market_timings WHERE exchange = ?").get(exchange) as any;
    if (!row) return null;
    return { openTime: row.open_time, closeTime: row.close_time };
  }

  addHoliday(date: string, exchange: string, description: string): void {
    this.db.run("INSERT OR REPLACE INTO market_holidays VALUES (?, ?, ?)", [date, exchange, description]);
    this.holidayCache.add(date);
  }

  setTimings(exchange: string, openTime: string, closeTime: string): void {
    this.db.run("INSERT OR REPLACE INTO market_timings (exchange, open_time, close_time) VALUES (?, ?, ?)",
      [exchange, openTime, closeTime]);
  }
}
```
