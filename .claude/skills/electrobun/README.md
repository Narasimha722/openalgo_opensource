# Electrobun Skill for Claude Code

A comprehensive [Claude Code](https://claude.ai/claude-code) skill for building large-scale algo trading desktop applications with [Electrobun](https://github.com/blackboardsh/electrobun).

## What's Included

### Core Electrobun Guides

| File | Description |
|------|-------------|
| `SKILL.md` | Main skill entry point — project structure, RPC patterns, core rules |
| `api-reference.md` | Complete Electrobun API reference (BrowserWindow, BrowserView, RPC, Tray, Menus, Utils, Events, etc.) |
| `architecture.md` | Application architecture, multi-window patterns, service design, error handling |
| `broker-integration.md` | Abstract broker interface, Zerodha/Kite implementation, order management, WebSocket binary parsing |
| `security.md` | Encryption at rest, keychain integration, sandboxing, navigation rules, rate limiting, audit logging |
| `websockets-realtime.md` | Market data streaming, reconnection, local API server, tick aggregation, connection monitoring |
| `storage.md` | SQLite setup (WAL mode), schema migrations, prepared queries, OHLC caching, config management |
| `performance.md` | Object pools, throttled broadcasting, ring buffers, virtual scrolling, memory monitoring |

### OpenAlgo Migration & Advanced Features

| File | Description |
|------|-------------|
| `openalgo-migration.md` | Full OpenAlgo → Electrobun migration guide with architecture mapping, RPC schema, 5-phase plan |
| `broker-plugin-system.md` | 29-broker plugin architecture, plugin registry, symbol mapping database, Zerodha example |
| `options-analytics.md` | Black-Scholes Greeks, implied volatility, max pain, put-call ratio, GEX, straddle pricing |
| `strategy-execution.md` | Python strategy subprocess execution, visual flow engine, webhooks, action center, Telegram, market calendar |
| `sandbox-paper-trading.md` | Paper trading engine, virtual capital management, margin simulation, live/sandbox order routing |
| `monitoring-logging.md` | Structured logging, API traffic tracking, latency percentiles, health checks, system metrics |

## Installation

### As a project skill (recommended)

Clone into your project's `.claude/skills/` directory:

```bash
cd your-electrobun-project
mkdir -p .claude/skills
git clone https://github.com/marketcalls/electrobun-skill.git .claude/skills/electrobun
```

### As a personal skill (available in all projects)

```bash
mkdir -p ~/.claude/skills
git clone https://github.com/marketcalls/electrobun-skill.git ~/.claude/skills/electrobun
```

## Usage

Once installed, the skill is available in Claude Code:

- **Auto-invocation** — Claude automatically loads the skill when it detects you're building an Electrobun app
- **Manual invocation** — Type `/electrobun` to explicitly load the skill context
- **With arguments** — Type `/electrobun dashboard` or `/electrobun order-manager` for targeted guidance

## What It Covers

### Electrobun APIs
- BrowserWindow, BrowserView, RPC (defineRPC), Electroview
- ApplicationMenu, ContextMenu, Tray, GlobalShortcut
- Utils (file dialogs, clipboard, notifications, paths)
- Screen, Session, Updater, BuildConfig, Events
- Webview tags, draggable regions, navigation rules, sandboxing
- CLI commands, build configuration, bundling & distribution

### Algo Trading Best Practices
- **Architecture** — Bun process for all trading logic, webview for UI only
- **Security** — AES-256-GCM encryption, TOTP/2FA, navigation lockdown, input validation
- **Broker Integration** — Abstract interface pattern, Zerodha/Kite implementation with binary WebSocket parsing
- **Real-Time Data** — WebSocket reconnection with state reconciliation, throttled UI updates
- **Storage** — SQLite with WAL mode, schema migrations, OHLC caching, audit trails
- **Performance** — Object pooling, ring buffers, typed arrays, virtual scrolling, batch DB writes
- **Risk Management** — Rate limiting, circuit breakers, position size limits, duplicate prevention

## Requirements

- [Claude Code](https://claude.ai/claude-code) CLI
- [Bun](https://bun.sh/) runtime
- [Electrobun](https://github.com/blackboardsh/electrobun) framework

## Resources

- [Electrobun Documentation](https://blackboard.sh/electrobun/docs/)
- [Electrobun GitHub](https://github.com/blackboardsh/electrobun)
- [Claude Code Skills Documentation](https://code.claude.com/docs/en/skills.md)

## License

MIT
