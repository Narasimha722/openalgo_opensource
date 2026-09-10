# Broker Integration Patterns for Electrobun Trading Apps

## Abstract Broker Interface

Design a broker-agnostic interface so you can swap brokers without changing the rest of the app.

```typescript
// src/bun/broker/interface.ts

export interface BrokerConnection {
  // Authentication
  authenticate(params: AuthParams): Promise<AuthResult>;
  logout(): Promise<void>;
  isAuthenticated(): boolean;

  // Market Data
  getLTP(symbols: string[]): Promise<Record<string, number>>;
  getQuote(symbol: string): Promise<QuoteData>;
  getOHLC(symbols: string[]): Promise<Record<string, OHLCData>>;
  getHistorical(params: HistoricalParams): Promise<OHLC[]>;

  // Orders
  placeOrder(params: OrderParams): Promise<OrderResult>;
  modifyOrder(params: ModifyOrderParams): Promise<ModifyResult>;
  cancelOrder(orderId: string, variety?: string): Promise<CancelResult>;
  getOrders(): Promise<Order[]>;
  getOrderHistory(orderId: string): Promise<OrderUpdate[]>;
  getTrades(): Promise<Trade[]>;

  // Portfolio
  getPositions(): Promise<Position[]>;
  getHoldings(): Promise<Holding[]>;
  getMargins(): Promise<MarginData>;

  // WebSocket
  connectWebSocket(): Promise<void>;
  disconnectWebSocket(): void;
  subscribe(symbols: string[], mode?: TickMode): void;
  unsubscribe(symbols: string[]): void;

  // Events
  on(event: "tick", handler: (data: TickData) => void): void;
  on(event: "order-update", handler: (data: OrderUpdate) => void): void;
  on(event: "error", handler: (error: Error) => void): void;
  on(event: "disconnect", handler: () => void): void;
  on(event: "reconnect", handler: () => void): void;
  off(event: string, handler: Function): void;

  // Instrument search
  searchInstruments(query: string): Promise<Instrument[]>;
}

// Common types
export interface AuthParams {
  apiKey: string;
  apiSecret: string;
  totp?: string;
  requestToken?: string;
}

export interface AuthResult {
  success: boolean;
  error?: string;
  accessToken?: string;
}

export interface OrderParams {
  symbol: string;
  exchange: "NSE" | "BSE" | "NFO" | "MCX" | "BFO";
  transactionType: "BUY" | "SELL";
  quantity: number;
  product: "CNC" | "MIS" | "NRML";
  orderType: "MARKET" | "LIMIT" | "SL" | "SL-M";
  price?: number;
  triggerPrice?: number;
  validity?: "DAY" | "IOC" | "TTL";
  variety?: "regular" | "amo" | "co" | "iceberg";
  tag?: string;
}

export interface Order {
  orderId: string;
  symbol: string;
  exchange: string;
  transactionType: "BUY" | "SELL";
  quantity: number;
  filledQuantity: number;
  pendingQuantity: number;
  price: number;
  averagePrice: number;
  triggerPrice: number;
  orderType: string;
  product: string;
  variety: string;
  status: OrderStatus;
  statusMessage: string;
  orderTimestamp: string;
  exchangeTimestamp: string;
  tag: string;
}

export type OrderStatus =
  | "OPEN"
  | "COMPLETE"
  | "CANCELLED"
  | "REJECTED"
  | "TRIGGER_PENDING"
  | "MODIFY_PENDING"
  | "CANCEL_PENDING";

export interface Position {
  symbol: string;
  exchange: string;
  product: string;
  quantity: number;
  buyQuantity: number;
  sellQuantity: number;
  buyPrice: number;
  sellPrice: number;
  averagePrice: number;
  lastPrice: number;
  pnl: number;
  realizedPnl: number;
  unrealizedPnl: number;
  multiplier: number;
}

export interface Holding {
  symbol: string;
  exchange: string;
  isin: string;
  quantity: number;
  averagePrice: number;
  lastPrice: number;
  pnl: number;
  dayChange: number;
  dayChangePercent: number;
}

export interface TickData {
  symbol: string;
  exchange: string;
  lastPrice: number;
  open: number;
  high: number;
  low: number;
  close: number;
  change: number;
  changePercent: number;
  volume: number;
  buyQuantity: number;
  sellQuantity: number;
  ohlc: { open: number; high: number; low: number; close: number };
  depth?: {
    buy: { price: number; quantity: number; orders: number }[];
    sell: { price: number; quantity: number; orders: number }[];
  };
  timestamp: number;
}

export type TickMode = "ltp" | "quote" | "full";

export interface OHLC {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  oi?: number;
}

export interface MarginData {
  equity: {
    available: number;
    used: number;
    total: number;
  };
  commodity: {
    available: number;
    used: number;
    total: number;
  };
}

export interface Instrument {
  instrumentToken: number;
  exchangeToken: number;
  tradingSymbol: string;
  name: string;
  exchange: string;
  segment: string;
  instrumentType: string;
  lotSize: number;
  tickSize: number;
  expiry?: string;
  strike?: number;
}
```

## Zerodha/Kite Broker Implementation

```typescript
// src/bun/broker/zerodha.ts
import type {
  BrokerConnection, AuthParams, AuthResult, OrderParams,
  OrderResult, Position, Holding, MarginData, TickData, OHLC
} from "./interface";

export class ZerodhaBroker implements BrokerConnection {
  private baseUrl = "https://api.kite.trade";
  private accessToken: string | null = null;
  private apiKey: string = "";
  private ws: WebSocket | null = null;
  private listeners = new Map<string, Set<Function>>();
  private reconnectAttempts = 0;
  private subscribedSymbols = new Map<string, number>(); // symbol -> instrument_token

  // --- Authentication ---

  async authenticate(params: AuthParams): Promise<AuthResult> {
    try {
      this.apiKey = params.apiKey;

      if (params.requestToken) {
        // Exchange request token for access token
        const response = await fetch(`${this.baseUrl}/session/token`, {
          method: "POST",
          headers: { "Content-Type": "application/x-www-form-urlencoded" },
          body: new URLSearchParams({
            api_key: params.apiKey,
            request_token: params.requestToken,
            checksum: await this.generateChecksum(params.apiKey, params.requestToken, params.apiSecret),
          }),
        });

        const data = await response.json();
        if (data.status === "success") {
          this.accessToken = data.data.access_token;
          return { success: true, accessToken: this.accessToken };
        }
        return { success: false, error: data.message };
      }

      return { success: false, error: "Request token required" };
    } catch (e) {
      return { success: false, error: e instanceof Error ? e.message : "Auth failed" };
    }
  }

  isAuthenticated(): boolean {
    return this.accessToken !== null;
  }

  async logout(): Promise<void> {
    if (this.accessToken) {
      await this.apiCall("DELETE", "/session/token", { access_token: this.accessToken });
      this.accessToken = null;
    }
    this.disconnectWebSocket();
  }

  // --- API Call Helper ---

  private async apiCall<T>(method: string, path: string, body?: any): Promise<T> {
    const url = `${this.baseUrl}${path}`;
    const headers: Record<string, string> = {
      "X-Kite-Version": "3",
      Authorization: `token ${this.apiKey}:${this.accessToken}`,
    };

    const options: RequestInit = { method, headers };

    if (body && method !== "GET") {
      headers["Content-Type"] = "application/x-www-form-urlencoded";
      options.body = new URLSearchParams(body);
    }

    const response = await fetch(url, options);

    if (response.status === 403) {
      this.accessToken = null;
      throw new Error("Session expired. Please re-authenticate.");
    }

    const data = await response.json();
    if (data.status !== "success") {
      throw new Error(data.message || `API error: ${response.status}`);
    }

    return data.data;
  }

  // --- Market Data ---

  async getLTP(symbols: string[]): Promise<Record<string, number>> {
    const instruments = symbols.map((s) => `i=${s}`).join("&");
    const data = await this.apiCall<Record<string, { last_price: number }>>(
      "GET", `/quote/ltp?${instruments}`
    );
    const result: Record<string, number> = {};
    for (const [key, val] of Object.entries(data)) {
      result[key] = val.last_price;
    }
    return result;
  }

  async getHistorical(params: {
    instrumentToken: number; from: string; to: string; interval: string;
  }): Promise<OHLC[]> {
    const data = await this.apiCall<{ candles: number[][] }>(
      "GET",
      `/instruments/historical/${params.instrumentToken}/${params.interval}?from=${params.from}&to=${params.to}`
    );
    return data.candles.map(([ts, o, h, l, c, v]) => ({
      timestamp: new Date(ts).toISOString(),
      open: o, high: h, low: l, close: c, volume: v,
    }));
  }

  // --- Orders ---

  async placeOrder(params: OrderParams): Promise<{ orderId: string; status: string }> {
    const data = await this.apiCall<{ order_id: string }>(
      "POST", `/orders/${params.variety || "regular"}`, {
        tradingsymbol: params.symbol,
        exchange: params.exchange,
        transaction_type: params.transactionType,
        quantity: String(params.quantity),
        product: params.product,
        order_type: params.orderType,
        price: params.price ? String(params.price) : undefined,
        trigger_price: params.triggerPrice ? String(params.triggerPrice) : undefined,
        validity: params.validity || "DAY",
        tag: params.tag,
      }
    );
    return { orderId: data.order_id, status: "OPEN" };
  }

  async cancelOrder(orderId: string, variety = "regular"): Promise<{ success: boolean }> {
    await this.apiCall("DELETE", `/orders/${variety}/${orderId}`);
    return { success: true };
  }

  async getOrders(): Promise<Order[]> {
    return await this.apiCall("GET", "/orders");
  }

  async getPositions(): Promise<Position[]> {
    const data = await this.apiCall<{ net: any[]; day: any[] }>("GET", "/portfolio/positions");
    return data.net.map(this.mapPosition);
  }

  async getHoldings(): Promise<Holding[]> {
    return await this.apiCall("GET", "/portfolio/holdings");
  }

  async getMargins(): Promise<MarginData> {
    const data = await this.apiCall<any>("GET", "/user/margins");
    return {
      equity: {
        available: data.equity?.available?.cash || 0,
        used: data.equity?.utilised?.debits || 0,
        total: data.equity?.net || 0,
      },
      commodity: {
        available: data.commodity?.available?.cash || 0,
        used: data.commodity?.utilised?.debits || 0,
        total: data.commodity?.net || 0,
      },
    };
  }

  // --- WebSocket Market Data ---

  async connectWebSocket(): Promise<void> {
    const wsUrl = `wss://ws.kite.trade?api_key=${this.apiKey}&access_token=${this.accessToken}`;
    this.ws = new WebSocket(wsUrl);

    this.ws.binaryType = "arraybuffer";

    this.ws.onopen = () => {
      this.reconnectAttempts = 0;
      this.emit("reconnect");
      // Re-subscribe after reconnect
      if (this.subscribedSymbols.size > 0) {
        const tokens = Array.from(this.subscribedSymbols.values());
        this.ws?.send(JSON.stringify({ a: "subscribe", v: tokens }));
      }
    };

    this.ws.onmessage = (event) => {
      if (typeof event.data === "string") {
        // JSON message (order updates, etc.)
        const data = JSON.parse(event.data);
        if (data.type === "order") {
          this.emit("order-update", data.data);
        }
      } else {
        // Binary tick data
        const ticks = this.parseBinaryTicks(event.data as ArrayBuffer);
        for (const tick of ticks) {
          this.emit("tick", tick);
        }
      }
    };

    this.ws.onclose = () => {
      this.emit("disconnect");
      this.reconnect();
    };

    this.ws.onerror = (err) => {
      this.emit("error", new Error("WebSocket error"));
    };
  }

  subscribe(symbols: string[], mode: TickMode = "quote"): void {
    // Map symbols to instrument tokens (you'd look these up from instruments list)
    const tokens = symbols.map((s) => this.subscribedSymbols.get(s)).filter(Boolean);
    if (tokens.length > 0 && this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ a: "subscribe", v: tokens }));
      const modeMap = { ltp: "ltp", quote: "quote", full: "full" };
      this.ws.send(JSON.stringify({ a: "mode", v: [modeMap[mode], tokens] }));
    }
  }

  private async reconnect(): Promise<void> {
    if (this.reconnectAttempts >= 10) {
      this.emit("error", new Error("Max reconnection attempts reached"));
      return;
    }
    const delay = Math.min(1000 * Math.pow(2, this.reconnectAttempts), 30000);
    this.reconnectAttempts++;
    await Bun.sleep(delay);
    await this.connectWebSocket();
  }

  disconnectWebSocket(): void {
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }

  // --- Binary tick parsing (Kite protocol) ---

  private parseBinaryTicks(buffer: ArrayBuffer): TickData[] {
    const view = new DataView(buffer);
    const numberOfPackets = view.getInt16(0);
    const ticks: TickData[] = [];
    let offset = 2;

    for (let i = 0; i < numberOfPackets; i++) {
      const packetLength = view.getInt16(offset);
      offset += 2;

      const instrumentToken = view.getInt32(offset);
      const lastPrice = view.getInt32(offset + 4) / 100;

      // Simplified — full parsing depends on tick mode
      ticks.push({
        symbol: this.getSymbolByToken(instrumentToken),
        exchange: "",
        lastPrice,
        open: 0, high: 0, low: 0, close: 0,
        change: 0, changePercent: 0,
        volume: 0, buyQuantity: 0, sellQuantity: 0,
        ohlc: { open: 0, high: 0, low: 0, close: 0 },
        timestamp: Date.now(),
      });

      offset += packetLength;
    }

    return ticks;
  }

  // --- Event Emitter ---

  on(event: string, handler: Function): void {
    if (!this.listeners.has(event)) this.listeners.set(event, new Set());
    this.listeners.get(event)!.add(handler);
  }

  off(event: string, handler: Function): void {
    this.listeners.get(event)?.delete(handler);
  }

  private emit(event: string, data?: any): void {
    const handlers = this.listeners.get(event);
    if (handlers) {
      for (const handler of handlers) handler(data);
    }
  }

  private getSymbolByToken(token: number): string {
    for (const [symbol, t] of this.subscribedSymbols) {
      if (t === token) return symbol;
    }
    return String(token);
  }

  private mapPosition(raw: any): Position {
    return {
      symbol: raw.tradingsymbol,
      exchange: raw.exchange,
      product: raw.product,
      quantity: raw.quantity,
      buyQuantity: raw.buy_quantity,
      sellQuantity: raw.sell_quantity,
      buyPrice: raw.buy_price,
      sellPrice: raw.sell_price,
      averagePrice: raw.average_price,
      lastPrice: raw.last_price,
      pnl: raw.pnl,
      realizedPnl: raw.realised,
      unrealizedPnl: raw.unrealised,
      multiplier: raw.multiplier,
    };
  }

  private async generateChecksum(apiKey: string, requestToken: string, apiSecret: string): Promise<string> {
    const input = `${apiKey}${requestToken}${apiSecret}`;
    const hash = new Bun.CryptoHasher("sha256");
    hash.update(input);
    return hash.digest("hex");
  }
}
```

## Broker Factory

```typescript
// src/bun/broker/factory.ts
import type { BrokerConnection } from "./interface";
import { ZerodhaBroker } from "./zerodha";

export type BrokerType = "zerodha" | "angel" | "fyers" | "upstox";

export function createBroker(type: BrokerType): BrokerConnection {
  switch (type) {
    case "zerodha":
      return new ZerodhaBroker();
    default:
      throw new Error(`Broker '${type}' not implemented`);
  }
}
```

## Order Manager

```typescript
// src/bun/services/order-manager.ts
import type { BrokerConnection, OrderParams, Order } from "../broker/interface";
import { Database } from "bun:sqlite";

export class OrderManager {
  private broker: BrokerConnection;
  private db: Database;
  private pendingOrders = new Map<string, OrderParams>();

  constructor(broker: BrokerConnection, db: Database) {
    this.broker = broker;
    this.db = db;

    // Listen for order updates
    broker.on("order-update", (update) => this.handleOrderUpdate(update));
  }

  async place(params: OrderParams): Promise<{ orderId: string; status: string }> {
    // Log intent
    this.logOrder("PLACE_INTENT", params);

    const result = await this.broker.placeOrder(params);
    this.pendingOrders.set(result.orderId, params);

    // Log result
    this.logOrder("PLACE_RESULT", { ...params, orderId: result.orderId, status: result.status });

    return result;
  }

  async cancel(orderId: string): Promise<{ success: boolean }> {
    this.logOrder("CANCEL_INTENT", { orderId });
    const result = await this.broker.cancelOrder(orderId);
    this.pendingOrders.delete(orderId);
    this.logOrder("CANCEL_RESULT", { orderId, ...result });
    return result;
  }

  async cancelAll(): Promise<{ cancelled: number; failed: number }> {
    const orders = await this.broker.getOrders();
    const open = orders.filter((o) => o.status === "OPEN" || o.status === "TRIGGER_PENDING");

    let cancelled = 0;
    let failed = 0;

    for (const order of open) {
      try {
        await this.broker.cancelOrder(order.orderId, order.variety);
        cancelled++;
      } catch {
        failed++;
      }
    }

    return { cancelled, failed };
  }

  async squareOffAll(): Promise<{ closed: number; failed: number }> {
    const positions = await this.broker.getPositions();
    const open = positions.filter((p) => p.quantity !== 0);

    let closed = 0;
    let failed = 0;

    for (const pos of open) {
      try {
        await this.broker.placeOrder({
          symbol: pos.symbol,
          exchange: pos.exchange as any,
          transactionType: pos.quantity > 0 ? "SELL" : "BUY",
          quantity: Math.abs(pos.quantity),
          product: pos.product as any,
          orderType: "MARKET",
        });
        closed++;
      } catch {
        failed++;
      }
    }

    return { closed, failed };
  }

  private handleOrderUpdate(update: any): void {
    this.logOrder("ORDER_UPDATE", update);
    if (update.status === "COMPLETE" || update.status === "CANCELLED" || update.status === "REJECTED") {
      this.pendingOrders.delete(update.orderId);
    }
  }

  private logOrder(action: string, data: any): void {
    this.db.run(
      "INSERT INTO order_log (timestamp, action, data) VALUES (?, ?, ?)",
      [Date.now(), action, JSON.stringify(data)]
    );
  }
}
```

## GTT (Good Till Triggered) Orders

```typescript
// Support for GTT orders — these persist on the broker's server
async placeGTT(params: {
  symbol: string;
  exchange: string;
  transactionType: "BUY" | "SELL";
  product: string;
  triggerType: "single" | "two-leg";
  triggerValue?: number;
  limitPrice?: number;
  quantity?: number;
  upperTrigger?: number;
  upperPrice?: number;
  upperQuantity?: number;
  lowerTrigger?: number;
  lowerPrice?: number;
  lowerQuantity?: number;
}): Promise<{ triggerId: number }> {
  // Implementation depends on broker API
  // Log to DB for tracking
  this.logOrder("GTT_PLACE", params);
  return await this.broker.placeGTT(params);
}
```

## Multi-Broker Support Pattern

```typescript
// Support multiple broker connections simultaneously
class BrokerManager {
  private brokers = new Map<string, BrokerConnection>();

  addBroker(id: string, broker: BrokerConnection): void {
    this.brokers.set(id, broker);
  }

  getBroker(id: string): BrokerConnection {
    const broker = this.brokers.get(id);
    if (!broker) throw new Error(`Broker ${id} not found`);
    return broker;
  }

  async getAggregatedPositions(): Promise<Position[]> {
    const allPositions: Position[] = [];
    for (const [id, broker] of this.brokers) {
      const positions = await broker.getPositions();
      allPositions.push(...positions.map((p) => ({ ...p, brokerId: id })));
    }
    return allPositions;
  }
}
```
