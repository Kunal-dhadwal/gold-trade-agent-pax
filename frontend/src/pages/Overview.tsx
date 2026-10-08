import React, { useState, useEffect } from 'react';
import {
  TrendingUp,
  TrendingDown,
  DollarSign,
  Shield,
  Activity,
  ArrowUpRight,
  ArrowDownRight,
} from 'lucide-react';
import { Portfolio, Position } from '../types';
import { api } from '../services/api';
import { wsClient } from '../services/websocket';

interface OverviewProps {
  accountId: string;
}

export const Overview: React.FC<OverviewProps> = ({ accountId }) => {
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [positions, setPositions] = useState<Position[]>([]);
  const [equityHistory, setEquityHistory] = useState<
    { timestamp: string; equity: number; balance: number; drawdown_pct: number }[]
  >([]);
  const [loading, setLoading] = useState(true);

  const loadData = async () => {
    try {
      const [port, pos, eq] = await Promise.all([
        api.getPortfolio(accountId),
        api.getPositions(accountId),
        api.getEquityHistory(accountId),
      ]);
      setPortfolio(port);
      setPositions(pos);
      setEquityHistory(eq);
    } catch (e) {
      console.error('Failed to load overview data:', e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
    const interval = setInterval(loadData, 4000);

    const unsub = wsClient.on('PORTFOLIO_UPDATED', (msg) => {
      if (msg.payload && msg.payload.account_id === accountId) {
        setPortfolio(msg.payload);
      }
    });

    return () => {
      clearInterval(interval);
      unsub();
    };
  }, [accountId]);

  if (loading && !portfolio) {
    return <div className="p-8 text-slate-400">Loading portfolio overview...</div>;
  }

  const p = portfolio || {
    account_id: accountId,
    balance: 10000.0,
    equity: 10000.0,
    margin_used: 0.0,
    margin_free: 10000.0,
    unrealized_pnl: 0.0,
    realized_pnl: 0.0,
    daily_pnl: 0.0,
    peak_equity: 10000.0,
    drawdown_pct: 0.0,
    updated_at: new Date().toISOString(),
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-bold text-slate-100">Paper Trading Portfolio Overview</h2>
        <p className="text-xs text-slate-400">Real-time ledger accounting, equity, and margin tracking</p>
      </div>

      {/* Metric Cards Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {/* Total Equity */}
        <div className="p-4 rounded-xl bg-slate-900 border border-slate-800">
          <div className="flex items-center justify-between text-xs text-slate-400 mb-1">
            <span>Total Equity</span>
            <DollarSign className="w-4 h-4 text-amber-400" />
          </div>
          <div className="text-2xl font-bold text-slate-100 font-mono">
            ${p.equity.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </div>
          <div className="mt-2 text-xs flex items-center space-x-1 text-slate-400">
            <span>Peak:</span>
            <span className="font-mono text-slate-300">${p.peak_equity.toFixed(2)}</span>
          </div>
        </div>

        {/* Daily P&L */}
        <div className="p-4 rounded-xl bg-slate-900 border border-slate-800">
          <div className="flex items-center justify-between text-xs text-slate-400 mb-1">
            <span>Daily Realized & Unrealized P&L</span>
            {p.daily_pnl >= 0 ? (
              <ArrowUpRight className="w-4 h-4 text-emerald-400" />
            ) : (
              <ArrowDownRight className="w-4 h-4 text-rose-400" />
            )}
          </div>
          <div
            className={`text-2xl font-bold font-mono ${
              p.daily_pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'
            }`}
          >
            {p.daily_pnl >= 0 ? '+' : ''}${p.daily_pnl.toFixed(2)}
          </div>
          <div className="mt-2 text-xs text-slate-400">Resets daily at 00:00:00 UTC</div>
        </div>

        {/* Unrealized P&L */}
        <div className="p-4 rounded-xl bg-slate-900 border border-slate-800">
          <div className="flex items-center justify-between text-xs text-slate-400 mb-1">
            <span>Unrealized Open P&L</span>
            <Activity className="w-4 h-4 text-amber-400" />
          </div>
          <div
            className={`text-2xl font-bold font-mono ${
              p.unrealized_pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'
            }`}
          >
            {p.unrealized_pnl >= 0 ? '+' : ''}${p.unrealized_pnl.toFixed(2)}
          </div>
          <div className="mt-2 text-xs text-slate-400 flex justify-between">
            <span>Realized:</span>
            <span className="font-mono text-slate-300">${p.realized_pnl.toFixed(2)}</span>
          </div>
        </div>

        {/* Peak-to-Trough Drawdown */}
        <div className="p-4 rounded-xl bg-slate-900 border border-slate-800">
          <div className="flex items-center justify-between text-xs text-slate-400 mb-1">
            <span>Max Drawdown</span>
            <Shield className="w-4 h-4 text-indigo-400" />
          </div>
          <div
            className={`text-2xl font-bold font-mono ${
              p.drawdown_pct > 6.0 ? 'text-rose-400' : p.drawdown_pct > 3.0 ? 'text-amber-400' : 'text-emerald-400'
            }`}
          >
            {p.drawdown_pct.toFixed(2)}%
          </div>
          <div className="mt-2 text-xs text-slate-400">Hard stop gate: 10.0%</div>
        </div>
      </div>

      {/* Margin Accounting Bar */}
      <div className="p-5 rounded-xl bg-slate-900 border border-slate-800 space-y-3">
        <div className="flex justify-between items-center text-xs">
          <span className="font-semibold text-slate-300">Margin Utilization & Free Capital</span>
          <span className="text-slate-400 font-mono">
            Reserved: ${p.margin_used.toFixed(2)} / Free: ${p.margin_free.toFixed(2)}
          </span>
        </div>
        <div className="w-full bg-slate-800 h-2.5 rounded-full overflow-hidden flex">
          <div
            className="bg-amber-500 h-full transition-all duration-500"
            style={{ width: `${Math.min((p.margin_used / Math.max(p.equity, 1)) * 100, 100)}%` }}
          ></div>
        </div>
        <div className="flex justify-between text-[11px] text-slate-500">
          <span>Cash Balance: ${p.balance.toFixed(2)}</span>
          <span>Settlement Currency: USDT</span>
        </div>
      </div>

      {/* Active Position Snapshot */}
      <div className="p-5 rounded-xl bg-slate-900 border border-slate-800">
        <h3 className="text-sm font-semibold text-slate-200 mb-3">Active Position Snapshot</h3>
        {positions.length === 0 ? (
          <div className="p-6 rounded-lg bg-slate-950/60 border border-slate-800 text-center text-xs text-slate-500">
            Flat — No active open positions in this account.
          </div>
        ) : (
          positions.map((pos) => (
            <div
              key={pos.position_id}
              className="p-4 rounded-lg bg-slate-950/80 border border-slate-800 flex flex-wrap items-center justify-between gap-4"
            >
              <div>
                <div className="flex items-center space-x-2">
                  <span
                    className={`px-2 py-0.5 rounded text-xs font-bold ${
                      pos.side === 'LONG'
                        ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30'
                        : 'bg-rose-500/20 text-rose-400 border border-rose-500/30'
                    }`}
                  >
                    {pos.side}
                  </span>
                  <span className="font-bold text-slate-200">{pos.symbol}</span>
                  <span className="text-xs text-slate-400 font-mono">{pos.quantity} contracts</span>
                </div>
                <div className="text-xs text-slate-400 mt-1">
                  Entry: ${pos.entry_price.toFixed(2)} | Mark: ${pos.current_price.toFixed(2)}
                </div>
              </div>

              <div className="text-right">
                <div
                  className={`text-lg font-bold font-mono ${
                    pos.unrealized_pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'
                  }`}
                >
                  {pos.unrealized_pnl >= 0 ? '+' : ''}${pos.unrealized_pnl.toFixed(2)}
                </div>
                <div className="text-xs text-slate-500">
                  SL: {pos.stop_loss ? `$${pos.stop_loss.toFixed(2)}` : 'None'} | TP:{' '}
                  {pos.take_profit ? `$${pos.take_profit.toFixed(2)}` : 'None'}
                </div>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
};

