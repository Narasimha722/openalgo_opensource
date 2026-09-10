# Broker Plugin System for Electrobun

Replicates OpenAlgo's 29-broker plugin architecture in TypeScript.

## Plugin Interface

Every broker implements this interface. Add a new broker by creating a new directory with these files.

```typescript
// src/bun/broker/types.ts

export interface BrokerPlugin {
  readonly name: string;
  readonly displayName: string;
  readonly authType: "oauth" | "totp" | "api_key";
  readonly exchanges: string[];

  // Authentication
  getLoginUrl(apiKey: string, redirectUrl: string): string;
  handleCallback(params: OAuthCallbackParams): Promise<AuthTokens>;
  refreshToken?(tokens: AuthTokens): Promise<AuthTokens>;
  revokeToken(tokens: AuthTokens): Promise<void>;
  isTokenValid(tokens: AuthTokens): boolean;

  // Orders
  placeOrder(params: BrokerOrderParams, tokens: AuthTokens): Promise<BrokerOrderResult>;
  modifyOrder(params: BrokerModifyParams, tokens: AuthTokens): Promise<BrokerModifyResult>;
  cancelOrder(orderId: string, variety: string, tokens: AuthTokens): Promise<{ success: boolean }>;
  getOrders(tokens: AuthTokens): Promise<BrokerOrder[]>;
  getOrderHistory(orderId: string, tokens: AuthTokens): Promise<BrokerOrderUpdate[]>;
  getTrades(tokens: AuthTokens): Promise<BrokerTrade[]>;

  // Portfolio
  getPositions(tokens: AuthTokens): Promise<BrokerPosition[]>;
  getHoldings(tokens: AuthTokens): Promise<BrokerHolding[]>;
  getFunds(tokens: AuthTokens): Promise<BrokerFunds>;

  // Market Data
  getQuote(symbol: string, exchange: string, tokens: AuthTokens): Promise<BrokerQuote>;
  getMultiQuotes(symbols: SymbolExchange[], tokens: AuthTokens): Promise<Record<string, BrokerQuote>>;
  getDepth(symbol: string, exchange: string, tokens: AuthTokens): Promise<BrokerDepth>;
  getHistory(params: BrokerHistoryParams, tokens: AuthTokens): Promise<BrokerOHLC[]>;

  // Options
  getOptionChain?(symbol: string, expiry: string, tokens: AuthTokens): Promise<BrokerOptionChain>;
  getExpiries?(symbol: string, exchange: string, tokens: AuthTokens): Promise<string[]>;

  // Instruments
  searchInstruments(query: string, tokens: AuthTokens): Promise<BrokerInstrument[]>;
  downloadMasterContract?(exchange: string, tokens: AuthTokens): Promise<BrokerInstrument[]>;

  // WebSocket
  createWebSocketAdapter(tokens: AuthTokens): BrokerWebSocketAdapter;

  // Symbol Mapping
  toOpenAlgoSymbol(brokerSymbol: string, exchange: string): string;
  toBrokerSymbol(oaSymbol: string, exchange: string): string;
}

export interface AuthTokens {
  accessToken: string;
  feedToken?: string;
  userId?: string;
  expiresAt?: number;
}

export interface OAuthCallbackParams {
  requestToken?: string;
  authCode?: string;
  apiKey: string;
  apiSecret: string;
}

export interface BrokerWebSocketAdapter {
  connect(): Promise<void>;
  disconnect(): void;
  subscribe(tokens: number[], mode: "ltp" | "quote" | "depth"): void;
  unsubscribe(tokens: number[]): void;
  on(event: "tick", handler: (data: NormalizedTick) => void): void;
  on(event: "order", handler: (data: BrokerOrder) => void): void;
  on(event: "error", handler: (error: Error) => void): void;
  on(event: "disconnect", handler: () => void): void;
  on(event: "reconnect", handler: () => void): void;
  isConnected(): boolean;
}

// Normalized tick format (all brokers map to this)
export interface NormalizedTick {
  instrumentToken: number;
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
  oi: number;
  buyQuantity: number;
  sellQuantity: number;
  depth?: {
    buy: { price: number; quantity: number; orders: number }[];
    sell: { price: number; quantity: number; orders: number }[];
  };
  timestamp: number;
}
```

## Plugin Directory Structure

```
src/bun/broker/
  types.ts                    # Shared interfaces
  registry.ts                 # Plugin registry
  symbol-mapper.ts            # Cross-broker symbol mapping
  zerodha/
    index.ts                  # BrokerPlugin implementation
    auth.ts                   # OAuth2 flow
    orders.ts                 # Order API calls
    data.ts                   # Market data API
    funds.ts                  # Funds & portfolio
    mapping.ts                # Symbol transformation
    websocket.ts              # WebSocket adapter
    master-contract.ts        # Instrument download
  angel/
    index.ts
    ...
  dhan/
    index.ts
    ...
  {26 more brokers}/
```

## Plugin Registry

```typescript
// src/bun/broker/registry.ts

import type { BrokerPlugin } from "./types";

class BrokerRegistry {
  private plugins = new Map<string, BrokerPlugin>();

  register(plugin: BrokerPlugin): void {
    this.plugins.set(plugin.name, plugin);
  }

  get(name: string): BrokerPlugin {
    const plugin = this.plugins.get(name);
    if (!plugin) throw new Error(`Broker '${name}' not registered`);
    return plugin;
  }

  getAll(): BrokerPlugin[] {
    return Array.from(this.plugins.values());
  }

  has(name: string): boolean {
    return this.plugins.has(name);
  }

  getAuthType(name: string): "oauth" | "totp" | "api_key" {
    return this.get(name).authType;
  }

  list(): { name: string; displayName: string; authType: string }[] {
    return this.getAll().map((p) => ({
      name: p.name,
      displayName: p.displayName,
      authType: p.authType,
    }));
  }
}

export const brokerRegistry = new BrokerRegistry();

// Auto-register all brokers on import
export async function loadBrokerPlugins(): Promise<void> {
  // Dynamic imports — only loads brokers you need
  const brokers = [
    () => import("./zerodha"),
    () => import("./angel"),
    () => import("./dhan"),
    () => import("./fyers"),
    () => import("./upstox"),
    // Add more as implemented
  ];

  for (const load of brokers) {
    try {
      const mod = await load();
      brokerRegistry.register(mod.default);
    } catch (e) {
      console.warn(`Failed to load broker plugin:`, e);
    }
  }
}
```

## Example: Zerodha Plugin

```typescript
// src/bun/broker/zerodha/index.ts
import type { BrokerPlugin, AuthTokens, OAuthCallbackParams, BrokerOrderParams, BrokerOrderResult } from "../types";
import { ZerodhaAuth } from "./auth";
import { ZerodhaOrders } from "./orders";
import { ZerodhaData } from "./data";
import { ZerodhaFunds } from "./funds";
import { ZerodhaMapping } from "./mapping";
import { ZerodhaWebSocket } from "./websocket";

const zerodha: BrokerPlugin = {
  name: "zerodha",
  displayName: "Zerodha (Kite)",
  authType: "oauth",
  exchanges: ["NSE", "BSE", "NFO", "MCX", "BFO", "CDS"],

  getLoginUrl: ZerodhaAuth.getLoginUrl,
  handleCallback: ZerodhaAuth.handleCallback,
  revokeToken: ZerodhaAuth.revokeToken,
  isTokenValid: ZerodhaAuth.isTokenValid,

  placeOrder: ZerodhaOrders.place,
  modifyOrder: ZerodhaOrders.modify,
  cancelOrder: ZerodhaOrders.cancel,
  getOrders: ZerodhaOrders.getAll,
  getOrderHistory: ZerodhaOrders.getHistory,
  getTrades: ZerodhaOrders.getTrades,

  getPositions: ZerodhaFunds.getPositions,
  getHoldings: ZerodhaFunds.getHoldings,
  getFunds: ZerodhaFunds.getFunds,

  getQuote: ZerodhaData.getQuote,
  getMultiQuotes: ZerodhaData.getMultiQuotes,
  getDepth: ZerodhaData.getDepth,
  getHistory: ZerodhaData.getHistory,
  getOptionChain: ZerodhaData.getOptionChain,
  getExpiries: ZerodhaData.getExpiries,

  searchInstruments: ZerodhaData.searchInstruments,
  downloadMasterContract: ZerodhaData.downloadMasterContract,

  createWebSocketAdapter: (tokens) => new ZerodhaWebSocket(tokens),

  toOpenAlgoSymbol: ZerodhaMapping.toOpenAlgo,
  toBrokerSymbol: ZerodhaMapping.toBroker,
};

export default zerodha;
```

```typescript
// src/bun/broker/zerodha/auth.ts
import type { AuthTokens, OAuthCallbackParams } from "../types";

const BASE_URL = "https://api.kite.trade";
const LOGIN_URL = "https://kite.zerodha.com/connect/login";

export const ZerodhaAuth = {
  getLoginUrl(apiKey: string, redirectUrl: string): string {
    return `${LOGIN_URL}?v=3&api_key=${apiKey}&redirect_url=${encodeURIComponent(redirectUrl)}`;
  },

  async handleCallback(params: OAuthCallbackParams): Promise<AuthTokens> {
    const checksum = new Bun.CryptoHasher("sha256")
      .update(`${params.apiKey}${params.requestToken}${params.apiSecret}`)
      .digest("hex");

    const response = await fetch(`${BASE_URL}/session/token`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        api_key: params.apiKey,
        request_token: params.requestToken!,
        checksum,
      }),
    });

    const data = await response.json();
    if (data.status !== "success") throw new Error(data.message);

    return {
      accessToken: data.data.access_token,
      userId: data.data.user_id,
    };
  },

  async revokeToken(tokens: AuthTokens): Promise<void> {
    await fetch(`${BASE_URL}/session/token`, {
      method: "DELETE",
      headers: { Authorization: `token ${tokens.accessToken}` },
    });
  },

  isTokenValid(tokens: AuthTokens): boolean {
    if (!tokens.accessToken) return false;
    if (tokens.expiresAt && Date.now() > tokens.expiresAt) return false;
    return true;
  },
};
```

```typescript
// src/bun/broker/zerodha/mapping.ts
// OpenAlgo uses "SBIN-EQ", Zerodha uses "SBIN"
export const ZerodhaMapping = {
  toBroker(oaSymbol: string, exchange: string): string {
    // Remove -EQ suffix for NSE/BSE equity
    if ((exchange === "NSE" || exchange === "BSE") && oaSymbol.endsWith("-EQ")) {
      return oaSymbol.replace("-EQ", "");
    }
    return oaSymbol;
  },

  toOpenAlgo(brokerSymbol: string, exchange: string): string {
    // Add -EQ suffix for NSE/BSE equity
    if ((exchange === "NSE" || exchange === "BSE") && !brokerSymbol.includes("-")) {
      return `${brokerSymbol}-EQ`;
    }
    return brokerSymbol;
  },

  // Map OpenAlgo product types to Zerodha
  mapProduct(oaProduct: string): string {
    const map: Record<string, string> = {
      CNC: "CNC", MIS: "MIS", NRML: "NRML",
    };
    return map[oaProduct] || oaProduct;
  },

  // Map OpenAlgo order types to Zerodha
  mapOrderType(oaType: string): string {
    const map: Record<string, string> = {
      MARKET: "MARKET", LIMIT: "LIMIT", SL: "SL", "SL-M": "SL-M",
    };
    return map[oaType] || oaType;
  },
};
```

## Symbol Mapping Database

```typescript
// src/bun/broker/symbol-mapper.ts
import { Database } from "bun:sqlite";

export class SymbolMapper {
  private db: Database;

  constructor(dbPath: string) {
    this.db = new Database(dbPath);
    this.db.run(`
      CREATE TABLE IF NOT EXISTS master_contract (
        broker TEXT NOT NULL,
        broker_symbol TEXT NOT NULL,
        oa_symbol TEXT NOT NULL,
        exchange TEXT NOT NULL,
        instrument_token INTEGER,
        name TEXT,
        lot_size INTEGER DEFAULT 1,
        tick_size REAL DEFAULT 0.05,
        instrument_type TEXT,
        expiry TEXT,
        strike REAL,
        PRIMARY KEY (broker, exchange, broker_symbol)
      )
    `);
    this.db.run("CREATE INDEX IF NOT EXISTS idx_oa_symbol ON master_contract(oa_symbol, exchange)");
    this.db.run("CREATE INDEX IF NOT EXISTS idx_token ON master_contract(broker, instrument_token)");
  }

  async loadMasterContract(broker: string, instruments: BrokerInstrument[]): Promise<number> {
    const insert = this.db.prepare(`
      INSERT OR REPLACE INTO master_contract
      (broker, broker_symbol, oa_symbol, exchange, instrument_token, name, lot_size, tick_size, instrument_type, expiry, strike)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `);

    const batch = this.db.transaction((items: BrokerInstrument[]) => {
      for (const inst of items) {
        insert.run(
          broker, inst.tradingSymbol, inst.oaSymbol, inst.exchange,
          inst.instrumentToken, inst.name, inst.lotSize, inst.tickSize,
          inst.instrumentType, inst.expiry || null, inst.strike || null
        );
      }
    });

    batch(instruments);
    return instruments.length;
  }

  getBrokerSymbol(broker: string, oaSymbol: string, exchange: string): string | null {
    const row = this.db.query(
      "SELECT broker_symbol FROM master_contract WHERE broker = ? AND oa_symbol = ? AND exchange = ?"
    ).get(broker, oaSymbol, exchange) as any;
    return row?.broker_symbol ?? null;
  }

  getOASymbol(broker: string, brokerSymbol: string, exchange: string): string | null {
    const row = this.db.query(
      "SELECT oa_symbol FROM master_contract WHERE broker = ? AND broker_symbol = ? AND exchange = ?"
    ).get(broker, brokerSymbol, exchange) as any;
    return row?.oa_symbol ?? null;
  }

  getInstrumentToken(broker: string, symbol: string, exchange: string): number | null {
    const row = this.db.query(
      "SELECT instrument_token FROM master_contract WHERE broker = ? AND oa_symbol = ? AND exchange = ?"
    ).get(broker, symbol, exchange) as any;
    return row?.instrument_token ?? null;
  }

  search(query: string, limit = 20): any[] {
    return this.db.query(
      "SELECT * FROM master_contract WHERE name LIKE ? OR oa_symbol LIKE ? LIMIT ?"
    ).all(`%${query}%`, `%${query}%`, limit);
  }
}
```

## Adding a New Broker

To add a new broker, create a directory and implement the `BrokerPlugin` interface:

```
src/bun/broker/newbroker/
  index.ts        # Export default BrokerPlugin
  auth.ts         # Authentication
  orders.ts       # Order operations
  data.ts         # Market data
  funds.ts        # Funds & portfolio
  mapping.ts      # Symbol transformation
  websocket.ts    # WebSocket adapter
```

Then register in `registry.ts`:
```typescript
const brokers = [
  // ... existing
  () => import("./newbroker"),
];
```
