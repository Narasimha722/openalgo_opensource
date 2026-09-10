# Security Best Practices for Electrobun Trading Apps

## Core Security Principles

1. **The webview is untrusted** — Treat it like a browser. All secrets, tokens, and sensitive operations stay in the Bun process.
2. **Defense in depth** — Multiple layers of protection (RPC validation, navigation rules, sandboxing, encryption at rest).
3. **Least privilege** — Each component only accesses what it needs.

## API Key and Secret Management

### Never Store Keys in Plain Text

```typescript
// src/bun/utils/crypto.ts
import { createCipheriv, createDecipheriv, randomBytes, scryptSync } from "crypto";

const ALGORITHM = "aes-256-gcm";

export function deriveKey(password: string, salt: Buffer): Buffer {
  return scryptSync(password, salt, 32);
}

export function encrypt(plaintext: string, password: string): string {
  const salt = randomBytes(16);
  const key = deriveKey(password, salt);
  const iv = randomBytes(12);
  const cipher = createCipheriv(ALGORITHM, key, iv);

  let encrypted = cipher.update(plaintext, "utf8", "hex");
  encrypted += cipher.final("hex");
  const authTag = cipher.getAuthTag();

  // salt:iv:authTag:ciphertext
  return `${salt.toString("hex")}:${iv.toString("hex")}:${authTag.toString("hex")}:${encrypted}`;
}

export function decrypt(encryptedData: string, password: string): string {
  const [saltHex, ivHex, authTagHex, ciphertext] = encryptedData.split(":");
  const salt = Buffer.from(saltHex, "hex");
  const iv = Buffer.from(ivHex, "hex");
  const authTag = Buffer.from(authTagHex, "hex");
  const key = deriveKey(password, salt);

  const decipher = createDecipheriv(ALGORITHM, key, iv);
  decipher.setAuthTag(authTag);

  let decrypted = decipher.update(ciphertext, "hex", "utf8");
  decrypted += decipher.final("utf8");
  return decrypted;
}
```

### Secure Credential Storage

```typescript
// src/bun/utils/config.ts
import { Database } from "bun:sqlite";
import { encrypt, decrypt } from "./crypto";

export class SecureConfig {
  private db: Database;
  private masterKey: string;

  constructor(dbPath: string, masterKey: string) {
    this.db = new Database(dbPath);
    this.masterKey = masterKey;
    this.db.run(`
      CREATE TABLE IF NOT EXISTS secure_config (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at INTEGER NOT NULL
      )
    `);
  }

  set(key: string, value: string): void {
    const encrypted = encrypt(value, this.masterKey);
    this.db.run(
      "INSERT OR REPLACE INTO secure_config (key, value, updated_at) VALUES (?, ?, ?)",
      [key, encrypted, Date.now()]
    );
  }

  get(key: string): string | null {
    const row = this.db.query("SELECT value FROM secure_config WHERE key = ?").get(key) as any;
    if (!row) return null;
    return decrypt(row.value, this.masterKey);
  }

  delete(key: string): void {
    this.db.run("DELETE FROM secure_config WHERE key = ?", [key]);
  }
}

// Usage in main process
const config = new SecureConfig(
  `${Utils.paths.userData}/config.db`,
  getMasterPassword() // Prompt user or use keychain
);

config.set("broker_api_key", "your-api-key");
config.set("broker_api_secret", "your-api-secret");
```

### macOS Keychain Integration

```typescript
// Use Bun.spawn to interact with the macOS security command
async function setKeychainItem(service: string, account: string, password: string): Promise<void> {
  const proc = Bun.spawn([
    "security", "add-generic-password",
    "-a", account,
    "-s", service,
    "-w", password,
    "-U", // Update if exists
  ]);
  await proc.exited;
}

async function getKeychainItem(service: string, account: string): Promise<string | null> {
  const proc = Bun.spawn([
    "security", "find-generic-password",
    "-a", account,
    "-s", service,
    "-w",
  ], { stdout: "pipe", stderr: "pipe" });

  const output = await new Response(proc.stdout).text();
  const exitCode = await proc.exited;
  return exitCode === 0 ? output.trim() : null;
}
```

## Navigation Rules — Restrict Webview Access

```typescript
// CRITICAL: Lock down what URLs the webview can access
const win = new BrowserWindow({
  title: "Trading",
  url: "views://mainview/index.html",
  // Only allow bundled views and specific broker API domains
  navigationRules: "views://*,^*",  // Allow views://, block everything else
  rpc,
});

// For windows that need broker OAuth login
const loginWindow = new BrowserWindow({
  title: "Broker Login",
  url: "https://kite.zerodha.com/connect/login?...",
  sandbox: true,  // No RPC in this window
  navigationRules: "https://kite.zerodha.com/*,https://kite.trade/*,^*",
});

// Listen for OAuth callback
loginWindow.webview.on("will-navigate", (e) => {
  const url = new URL(e.data.detail);
  if (url.hostname === "your-callback-domain.com") {
    const requestToken = url.searchParams.get("request_token");
    handleOAuthCallback(requestToken);
    loginWindow.close();
  }
});
```

## Sandboxing External Content

```typescript
// Sandbox mode disables RPC entirely — use for untrusted content
const chartWindow = new BrowserWindow({
  title: "TradingView Chart",
  url: "https://www.tradingview.com/chart/",
  sandbox: true,
  navigationRules: "https://www.tradingview.com/*,https://*.tradingview.com/*,^*",
});

// Embedded sandboxed webview
// <electrobun-webview src="https://external-chart.com" sandbox></electrobun-webview>
```

## RPC Input Validation

```typescript
// ALWAYS validate RPC inputs in the Bun handler — never trust the webview
const rpc = BrowserView.defineRPC<TradingRPC>({
  maxRequestTime: 30000,
  handlers: {
    requests: {
      placeOrder: async (params) => {
        // Validate every field
        if (!params.symbol || typeof params.symbol !== "string") {
          throw new Error("Invalid symbol");
        }
        if (!params.quantity || params.quantity <= 0 || !Number.isInteger(params.quantity)) {
          throw new Error("Invalid quantity");
        }
        if (!["BUY", "SELL"].includes(params.transactionType)) {
          throw new Error("Invalid transaction type");
        }
        if (!["MARKET", "LIMIT", "SL", "SL-M"].includes(params.orderType)) {
          throw new Error("Invalid order type");
        }
        if (params.orderType === "LIMIT" && (!params.price || params.price <= 0)) {
          throw new Error("Limit order requires valid price");
        }

        // Risk checks
        await riskManager.validateOrder(params);

        // Only then send to broker
        return await broker.placeOrder(params);
      },
    },
    messages: {},
  },
});
```

## Session and Partition Isolation

```typescript
// Use separate session partitions for different concerns
const tradingWindow = new BrowserWindow({
  url: "views://mainview/index.html",
  rpc: tradingRpc,
  // Default partition — isolated from external browsing
});

// Broker login in separate partition
const loginView = new BrowserView({
  url: "https://broker-login.com",
  partition: "persist:broker-auth",
  sandbox: true,
});

// Clear broker session on logout
const session = Session.fromPartition("persist:broker-auth");
await session.cookies.clear();
await session.clearStorageData(["all"]);
```

## Rate Limiting and Circuit Breakers

```typescript
// src/bun/services/risk-manager.ts
export class RiskManager {
  private orderCounts = new Map<string, { count: number; resetAt: number }>();
  private maxOrdersPerMinute = 20;
  private maxLossPerDay: number;
  private maxPositionSize: number;
  private dailyPnL = 0;

  constructor(config: RiskConfig) {
    this.maxLossPerDay = config.maxLossPerDay;
    this.maxPositionSize = config.maxPositionSize;
  }

  async validateOrder(params: OrderParams): Promise<void> {
    // Rate limit
    this.checkRateLimit();

    // Position size limit
    if (params.quantity > this.maxPositionSize) {
      throw new Error(`Quantity ${params.quantity} exceeds max position size ${this.maxPositionSize}`);
    }

    // Daily loss circuit breaker
    if (this.dailyPnL < -this.maxLossPerDay) {
      throw new Error(`Daily loss limit reached: ${this.dailyPnL.toFixed(2)}`);
    }

    // Duplicate order prevention
    if (await this.isDuplicateOrder(params)) {
      throw new Error("Duplicate order detected within cooldown period");
    }
  }

  private checkRateLimit(): void {
    const now = Date.now();
    const key = "global";
    const entry = this.orderCounts.get(key);

    if (!entry || now >= entry.resetAt) {
      this.orderCounts.set(key, { count: 1, resetAt: now + 60000 });
      return;
    }

    if (entry.count >= this.maxOrdersPerMinute) {
      throw new Error(`Rate limit: max ${this.maxOrdersPerMinute} orders/minute`);
    }

    entry.count++;
  }

  private async isDuplicateOrder(params: OrderParams): Promise<boolean> {
    // Check if same symbol+type+quantity was submitted in last 2 seconds
    const recent = this.recentOrders.filter(
      (o) =>
        o.symbol === params.symbol &&
        o.transactionType === params.transactionType &&
        o.quantity === params.quantity &&
        Date.now() - o.timestamp < 2000
    );
    return recent.length > 0;
  }

  updatePnL(pnl: number): void {
    this.dailyPnL = pnl;
  }
}
```

## Audit Logging

```typescript
// Log every sensitive operation for compliance and debugging
export class AuditLogger {
  private db: Database;
  private insertStmt: Statement;

  constructor(dbPath: string) {
    this.db = new Database(dbPath);
    this.db.run(`
      CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp INTEGER NOT NULL,
        action TEXT NOT NULL,
        details TEXT,
        user_agent TEXT,
        result TEXT,
        error TEXT
      )
    `);
    this.insertStmt = this.db.prepare(
      "INSERT INTO audit_log (timestamp, action, details, result, error) VALUES (?, ?, ?, ?, ?)"
    );
  }

  log(action: string, details: unknown, result?: unknown, error?: string): void {
    this.insertStmt.run(
      Date.now(),
      action,
      JSON.stringify(details),
      result ? JSON.stringify(result) : null,
      error ?? null
    );
  }
}

// Usage in order handler
audit.log("place_order", params);
try {
  const result = await broker.placeOrder(params);
  audit.log("order_placed", params, result);
  return result;
} catch (e) {
  audit.log("order_failed", params, null, e.message);
  throw e;
}
```

## Secure Communication Architecture

Electrobun's RPC transport is already encrypted (AES-256-GCM over localhost WebSocket), but for defense in depth:

```typescript
// 1. Navigation rules restrict webview to views:// only
// 2. Sandbox any external content
// 3. Validate all RPC inputs on the Bun side
// 4. Rate-limit all sensitive operations
// 5. Encrypt credentials at rest
// 6. Audit-log all operations
// 7. Use session partitions for isolation
// 8. Never expose broker tokens to the webview
```

## TOTP / 2FA Handling

```typescript
// Handle TOTP for broker login (Zerodha requires it)
import { createHmac } from "crypto";

function generateTOTP(secret: string): string {
  const time = Math.floor(Date.now() / 1000 / 30);
  const buffer = Buffer.alloc(8);
  buffer.writeBigInt64BE(BigInt(time));

  const hmac = createHmac("sha1", Buffer.from(secret, "base32"));
  hmac.update(buffer);
  const hash = hmac.digest();

  const offset = hash[hash.length - 1] & 0xf;
  const code = (hash.readUInt32BE(offset) & 0x7fffffff) % 1000000;
  return code.toString().padStart(6, "0");
}

// In RPC handler
login: async ({ apiKey, apiSecret, totpSecret }) => {
  const totp = generateTOTP(totpSecret);
  return await broker.login(apiKey, apiSecret, totp);
},
```

## Graceful Shutdown Security

```typescript
import Electrobun from "electrobun/bun";

// Ensure clean shutdown — don't leave open orders or connections
Electrobun.events.on("before-quit", async (e) => {
  console.log("Shutting down gracefully...");

  // Cancel all pending orders
  try {
    await orderManager.cancelAllPending();
  } catch (err) {
    console.error("Failed to cancel pending orders:", err);
  }

  // Disconnect WebSocket cleanly
  marketDataService.disconnect();

  // Close database connections
  db.close();

  // Clear sensitive data from memory
  secureConfig = null;
});
```
