# Options Analytics for Electrobun Trading Apps

Covers option chain display, Greeks calculation, IV charting, OI analysis, max pain, straddle charts, volatility surface, and GEX.

## Option Chain with Live Data

```typescript
// src/bun/services/option-chain.ts
import type { BrokerPlugin, AuthTokens } from "../broker/types";

interface OptionStrike {
  strikePrice: number;
  call: OptionContract | null;
  put: OptionContract | null;
}

interface OptionContract {
  symbol: string;
  ltp: number;
  change: number;
  changePercent: number;
  volume: number;
  oi: number;
  oiChange: number;
  bidPrice: number;
  askPrice: number;
  bidQty: number;
  askQty: number;
  iv: number;
  delta: number;
  gamma: number;
  theta: number;
  vega: number;
}

export class OptionChainService {
  constructor(private broker: BrokerPlugin, private tokens: AuthTokens) {}

  async getChain(symbol: string, expiry: string): Promise<OptionStrike[]> {
    const raw = await this.broker.getOptionChain!(symbol, expiry, this.tokens);
    return this.buildStrikeTable(raw);
  }

  async getExpiries(symbol: string, exchange: string): Promise<string[]> {
    return this.broker.getExpiries!(symbol, exchange, this.tokens);
  }

  private buildStrikeTable(raw: any): OptionStrike[] {
    const strikes = new Map<number, OptionStrike>();

    for (const contract of raw.contracts || []) {
      const strike = contract.strikePrice;
      if (!strikes.has(strike)) {
        strikes.set(strike, { strikePrice: strike, call: null, put: null });
      }

      const entry = strikes.get(strike)!;
      const option: OptionContract = {
        symbol: contract.symbol,
        ltp: contract.lastPrice,
        change: contract.change,
        changePercent: contract.changePercent,
        volume: contract.volume,
        oi: contract.oi,
        oiChange: contract.oiChange || 0,
        bidPrice: contract.bidPrice,
        askPrice: contract.askPrice,
        bidQty: contract.bidQty,
        askQty: contract.askQty,
        iv: contract.iv || 0,
        delta: 0, gamma: 0, theta: 0, vega: 0,
      };

      if (contract.optionType === "CE") entry.call = option;
      else entry.put = option;
    }

    return Array.from(strikes.values()).sort((a, b) => a.strikePrice - b.strikePrice);
  }
}
```

## Greeks Calculation (Black-Scholes)

```typescript
// src/bun/services/greeks.ts

// Standard normal CDF approximation (Abramowitz & Stegun)
function normalCDF(x: number): number {
  const a1 = 0.254829592, a2 = -0.284496736, a3 = 1.421413741;
  const a4 = -1.453152027, a5 = 1.061405429, p = 0.3275911;
  const sign = x < 0 ? -1 : 1;
  x = Math.abs(x) / Math.SQRT2;
  const t = 1.0 / (1.0 + p * x);
  const y = 1 - ((((a5 * t + a4) * t + a3) * t + a2) * t + a1) * t * Math.exp(-x * x);
  return 0.5 * (1 + sign * y);
}

function normalPDF(x: number): number {
  return Math.exp(-0.5 * x * x) / Math.sqrt(2 * Math.PI);
}

interface GreeksInput {
  spotPrice: number;
  strikePrice: number;
  timeToExpiry: number;  // In years (days/365)
  riskFreeRate: number;  // e.g., 0.07 for 7%
  volatility: number;    // e.g., 0.20 for 20% IV
  optionType: "CE" | "PE";
}

interface GreeksOutput {
  price: number;
  delta: number;
  gamma: number;
  theta: number;
  vega: number;
  rho: number;
  iv?: number;
}

export function calculateGreeks(input: GreeksInput): GreeksOutput {
  const { spotPrice: S, strikePrice: K, timeToExpiry: T, riskFreeRate: r, volatility: sigma, optionType } = input;

  if (T <= 0) {
    // Expired
    const intrinsic = optionType === "CE"
      ? Math.max(S - K, 0)
      : Math.max(K - S, 0);
    return { price: intrinsic, delta: optionType === "CE" ? (S > K ? 1 : 0) : (S < K ? -1 : 0), gamma: 0, theta: 0, vega: 0, rho: 0 };
  }

  const sqrtT = Math.sqrt(T);
  const d1 = (Math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrtT);
  const d2 = d1 - sigma * sqrtT;

  const Nd1 = normalCDF(d1);
  const Nd2 = normalCDF(d2);
  const nd1 = normalPDF(d1);
  const expRT = Math.exp(-r * T);

  let price: number, delta: number, rho: number;

  if (optionType === "CE") {
    price = S * Nd1 - K * expRT * Nd2;
    delta = Nd1;
    rho = K * T * expRT * Nd2 / 100;
  } else {
    price = K * expRT * normalCDF(-d2) - S * normalCDF(-d1);
    delta = Nd1 - 1;
    rho = -K * T * expRT * normalCDF(-d2) / 100;
  }

  const gamma = nd1 / (S * sigma * sqrtT);
  const theta = -(S * nd1 * sigma) / (2 * sqrtT) - r * K * expRT * (optionType === "CE" ? Nd2 : normalCDF(-d2));
  const vega = S * nd1 * sqrtT / 100;

  return {
    price,
    delta,
    gamma,
    theta: theta / 365, // Per day
    vega,
    rho,
  };
}

// Implied Volatility via Newton-Raphson
export function calculateIV(
  marketPrice: number,
  spotPrice: number,
  strikePrice: number,
  timeToExpiry: number,
  riskFreeRate: number,
  optionType: "CE" | "PE"
): number {
  let sigma = 0.3; // Initial guess
  const maxIterations = 100;
  const tolerance = 0.0001;

  for (let i = 0; i < maxIterations; i++) {
    const result = calculateGreeks({
      spotPrice, strikePrice, timeToExpiry, riskFreeRate,
      volatility: sigma, optionType,
    });

    const diff = result.price - marketPrice;
    if (Math.abs(diff) < tolerance) break;

    const vega = result.vega * 100; // Undo the /100 in vega calc
    if (Math.abs(vega) < 1e-10) break;

    sigma -= diff / vega;
    sigma = Math.max(0.01, Math.min(5.0, sigma)); // Clamp to reasonable range
  }

  return sigma;
}

// Batch Greeks for option chain
export function enrichChainWithGreeks(
  chain: OptionStrike[],
  spotPrice: number,
  timeToExpiry: number,
  riskFreeRate: number
): OptionStrike[] {
  return chain.map((strike) => {
    if (strike.call) {
      const iv = strike.call.iv > 0 ? strike.call.iv / 100 : calculateIV(
        strike.call.ltp, spotPrice, strike.strikePrice, timeToExpiry, riskFreeRate, "CE"
      );
      const greeks = calculateGreeks({
        spotPrice, strikePrice: strike.strikePrice, timeToExpiry, riskFreeRate,
        volatility: iv, optionType: "CE",
      });
      strike.call = { ...strike.call, iv: iv * 100, delta: greeks.delta, gamma: greeks.gamma, theta: greeks.theta, vega: greeks.vega };
    }

    if (strike.put) {
      const iv = strike.put.iv > 0 ? strike.put.iv / 100 : calculateIV(
        strike.put.ltp, spotPrice, strike.strikePrice, timeToExpiry, riskFreeRate, "PE"
      );
      const greeks = calculateGreeks({
        spotPrice, strikePrice: strike.strikePrice, timeToExpiry, riskFreeRate,
        volatility: iv, optionType: "PE",
      });
      strike.put = { ...strike.put, iv: iv * 100, delta: greeks.delta, gamma: greeks.gamma, theta: greeks.theta, vega: greeks.vega };
    }

    return strike;
  });
}
```

## Max Pain Calculation

```typescript
// Max pain = strike price where total loss for option writers is minimum
export function calculateMaxPain(chain: OptionStrike[]): { maxPainStrike: number; data: MaxPainData[] } {
  const strikes = chain.map((s) => s.strikePrice);
  const data: MaxPainData[] = [];

  for (const testStrike of strikes) {
    let callWriterLoss = 0;
    let putWriterLoss = 0;

    for (const strike of chain) {
      // Call writers lose when spot > strike
      if (testStrike > strike.strikePrice && strike.call) {
        callWriterLoss += (testStrike - strike.strikePrice) * strike.call.oi;
      }
      // Put writers lose when spot < strike
      if (testStrike < strike.strikePrice && strike.put) {
        putWriterLoss += (strike.strikePrice - testStrike) * strike.put.oi;
      }
    }

    data.push({
      strike: testStrike,
      callWriterLoss,
      putWriterLoss,
      totalLoss: callWriterLoss + putWriterLoss,
    });
  }

  const minLoss = data.reduce((min, d) => d.totalLoss < min.totalLoss ? d : min, data[0]);
  return { maxPainStrike: minLoss.strike, data };
}

interface MaxPainData {
  strike: number;
  callWriterLoss: number;
  putWriterLoss: number;
  totalLoss: number;
}
```

## Put-Call Ratio (PCR)

```typescript
export function calculatePCR(chain: OptionStrike[]): { oiPCR: number; volumePCR: number } {
  let totalPutOI = 0, totalCallOI = 0;
  let totalPutVol = 0, totalCallVol = 0;

  for (const strike of chain) {
    if (strike.put) { totalPutOI += strike.put.oi; totalPutVol += strike.put.volume; }
    if (strike.call) { totalCallOI += strike.call.oi; totalCallVol += strike.call.volume; }
  }

  return {
    oiPCR: totalCallOI > 0 ? totalPutOI / totalCallOI : 0,
    volumePCR: totalCallVol > 0 ? totalPutVol / totalCallVol : 0,
  };
}
```

## GEX (Gamma Exposure)

```typescript
// GEX estimates the aggregate gamma exposure of market makers
export function calculateGEX(chain: OptionStrike[], spotPrice: number): GEXData[] {
  return chain.map((strike) => {
    const callGamma = strike.call ? strike.call.gamma * strike.call.oi * 100 * spotPrice * spotPrice * 0.01 : 0;
    const putGamma = strike.put ? strike.put.gamma * strike.put.oi * 100 * spotPrice * spotPrice * 0.01 : 0;

    // Call OI = positive gamma (dealers hedging calls), Put OI = negative gamma
    const netGEX = callGamma - putGamma;

    return {
      strike: strike.strikePrice,
      callGEX: callGamma,
      putGEX: -putGamma,
      netGEX,
    };
  });
}

interface GEXData {
  strike: number;
  callGEX: number;
  putGEX: number;
  netGEX: number;
}
```

## Straddle/Strangle Pricing

```typescript
export function calculateStraddle(chain: OptionStrike[], atmStrike: number): StraddleData | null {
  const strike = chain.find((s) => s.strikePrice === atmStrike);
  if (!strike || !strike.call || !strike.put) return null;

  return {
    strike: atmStrike,
    callPrice: strike.call.ltp,
    putPrice: strike.put.ltp,
    straddlePrice: strike.call.ltp + strike.put.ltp,
    upperBreakeven: atmStrike + strike.call.ltp + strike.put.ltp,
    lowerBreakeven: atmStrike - (strike.call.ltp + strike.put.ltp),
    totalIV: (strike.call.iv + strike.put.iv) / 2,
    totalOI: strike.call.oi + strike.put.oi,
  };
}

interface StraddleData {
  strike: number;
  callPrice: number;
  putPrice: number;
  straddlePrice: number;
  upperBreakeven: number;
  lowerBreakeven: number;
  totalIV: number;
  totalOI: number;
}
```

## Wiring Options to Webview

```typescript
// In RPC handlers (src/bun/index.ts)
const optionService = new OptionChainService(broker, tokens);

// RPC request handlers:
getOptionChain: async ({ symbol, expiry }) => {
  const chain = await optionService.getChain(symbol, expiry);
  const spotLTP = await broker.getQuote(symbol, "NSE", tokens);
  const daysToExpiry = Math.max(1, daysBetween(new Date(), new Date(expiry)));
  const enriched = enrichChainWithGreeks(chain, spotLTP.lastPrice, daysToExpiry / 365, 0.07);
  const maxPain = calculateMaxPain(enriched);
  const pcr = calculatePCR(enriched);
  const gex = calculateGEX(enriched, spotLTP.lastPrice);

  return { chain: enriched, maxPain, pcr, gex, spotPrice: spotLTP.lastPrice };
},

getExpiries: async ({ symbol, exchange }) => {
  return optionService.getExpiries(symbol, exchange);
},
```
