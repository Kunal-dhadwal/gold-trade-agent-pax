import React, { useState, useEffect } from 'react';
import { CandlestickChart, Activity, Radio, Clock } from 'lucide-react';
import { wsClient } from '../services/websocket';

export const LiveChart: React.FC = () => {
  const [candles, setCandles] = useState<any[]>([
    { timestamp: '12:00', open: 2712.5, high: 2716.0, low: 2711.2, close: 2714.8, volume: 1420 },
    { timestamp: '12:05', open: 2714.8, high: 2718.5, low: 2713.8, close: 2717.2, volume: 1850 },
    { timestamp: '12:10', open: 2717.2, high: 2721.0, low: 2716.5, close: 2719.6, volume: 2100 },
    { timestamp: '12:15', open: 2719.6, high: 2722.8, low: 2718.9, close: 2720.5, volume: 1650 },
    { timestamp: '12:20', open: 2720.5, high: 2721.4, low: 2717.0, close: 2718.1, volume: 1320 },
  ]);
  const [latestMark, setLatestMark] = useState<number>(2718.1);
  const [spread, setSpread] = useState<number>(0.2);

  useEffect(() => {
    const unsub = wsClient.on('all', (msg) => {
      if (msg.type === 'CANDLE_UPDATE' && msg.candle) {
        setLatestMark(msg.candle.close);
        if (msg.candle.spread) setSpread(msg.candle.spread);
        setCandles((prev) => [...prev.slice(-19), msg.candle]);
      }
    });
    return () => unsub();
  }, []);

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-xl font-bold text-slate-100 flex items-center space-x-2">
            <span>PAXGUSDT — 5m Gold Derivative Stream</span>
            <span className="flex items-center space-x-1 text-xs px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-400 border border-emerald-500/30">
              <Radio className="w-3 h-3 animate-pulse" />
              <span>DELTA WS LIVE</span>
            </span>
          </h2>
          <p className="text-xs text-slate-400">Next-Bar Open Model A Real-Time Price Action</p>
        </div>

        <div className="flex items-center space-x-4 text-right">
          <div>
            <div className="text-xs text-slate-400">Mark Price</div>
            <div className="text-2xl font-bold font-mono text-amber-400">${latestMark.toFixed(2)}</div>
          </div>
          <div>
            <div className="text-xs text-slate-400">Bid/Ask Spread</div>
            <div className="text-sm font-semibold font-mono text-slate-200">${spread.toFixed(2)}</div>
          </div>
        </div>
      </div>

      {/* Visual Chart Canvas Area */}
      <div className="p-6 rounded-xl bg-slate-900 border border-slate-800 space-y-4">
        <div className="flex justify-between items-center text-xs text-slate-400 border-b border-slate-800 pb-3">
          <div className="flex items-center space-x-4 font-mono">
            <span>O: ${candles[candles.length - 1]?.open.toFixed(2)}</span>
            <span>H: ${candles[candles.length - 1]?.high.toFixed(2)}</span>
            <span>L: ${candles[candles.length - 1]?.low.toFixed(2)}</span>
            <span>C: ${candles[candles.length - 1]?.close.toFixed(2)}</span>
            <span>Vol: {candles[candles.length - 1]?.volume}</span>
          </div>
          <div className="flex items-center space-x-2">
            <span className="w-2 h-2 rounded-full bg-emerald-400"></span>
            <span>Model A: Execution at T+1 Open</span>
          </div>
        </div>

        {/* CSS Candlestick Bars Preview */}
        <div className="h-64 flex items-end justify-between px-6 py-4 bg-slate-950/80 rounded-lg border border-slate-800/80">
          {candles.map((c, idx) => {
            const isGreen = c.close >= c.open;
            const heightPct = Math.max(Math.abs(c.close - c.open) * 12, 10);
            return (
              <div key={idx} className="flex flex-col items-center group relative cursor-pointer">
                <div
                  className={`w-0.5 h-32 ${isGreen ? 'bg-emerald-500/40' : 'bg-rose-500/40'}`}
                ></div>
                <div
                  className={`w-6 rounded-sm ${
                    isGreen ? 'bg-emerald-500 border border-emerald-400' : 'bg-rose-500 border border-rose-400'
                  }`}
                  style={{ height: `${heightPct}px` }}
                ></div>
                <span className="text-[10px] text-slate-500 font-mono mt-2">{c.timestamp}</span>

                {/* Tooltip on hover */}
                <div className="absolute bottom-full mb-2 hidden group-hover:block bg-slate-900 border border-slate-700 p-2 rounded text-[11px] font-mono shadow-xl z-20 whitespace-nowrap">
                  <div>Open: ${c.open.toFixed(2)}</div>
                  <div>High: ${c.high.toFixed(2)}</div>
                  <div>Low: ${c.low.toFixed(2)}</div>
                  <div>Close: ${c.close.toFixed(2)}</div>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Signal Execution Notice */}
      <div className="p-4 rounded-xl bg-amber-500/10 border border-amber-500/30 flex items-start space-x-3 text-xs text-amber-300">
        <Clock className="w-4 h-4 shrink-0 text-amber-400 mt-0.5" />
        <div>
          <span className="font-semibold">Deterministic Execution Timing:</span> When a strategy triggers a signal at 5m
          bar close (T), the order is submitted immediately and matched virtual-instantly at the Next-Bar Open (T+1 Open)
          quote, applying half-spread and volatility slippage. Intra-bar bar-close fills are strictly forbidden in paper
          trading.
        </div>
      </div>
    </div>
  );
};

