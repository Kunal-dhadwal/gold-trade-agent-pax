import React, { useState, useEffect } from 'react';
import { History, Download, ArrowUpRight, ArrowDownRight } from 'lucide-react';
import { PaperTrade } from '../types';
import { api } from '../services/api';

export const TradeJournal: React.FC = () => {
  const [trades, setTrades] = useState<PaperTrade[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const loadTrades = async () => {
      try {
        const data = await api.getTrades();
        setTrades(data);
      } catch (e) {
        console.error('Failed to load trade journal:', e);
      } finally {
        setLoading(false);
      }
    };
    loadTrades();
  }, []);

  const exportCSV = () => {
    if (trades.length === 0) return;
    const headers = ['Trade ID', 'Symbol', 'Side', 'Qty', 'Entry Price', 'Exit Price', 'P&L', 'Fee', 'Slippage', 'Entry Time', 'Exit Time'];
    const rows = trades.map((t) => [
      t.trade_id,
      t.symbol,
      t.side,
      t.quantity,
      t.entry_price,
      t.exit_price,
      t.pnl,
      t.fee,
      t.slippage,
      t.entry_time,
      t.exit_time,
    ]);
    const csvContent = 'data:text/csv;charset=utf-8,' + [headers.join(','), ...rows.map((e) => e.join(','))].join('\n');
    const encodedUri = encodeURI(csvContent);
    const link = document.createElement('a');
    link.setAttribute('href', encodedUri);
    link.setAttribute('download', `paper_trades_${new Date().toISOString().slice(0, 10)}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  const totalPnl = trades.reduce((acc, t) => acc + t.pnl, 0);
  const winCount = trades.filter((t) => t.pnl > 0).length;
  const winRate = trades.length > 0 ? (winCount / trades.length) * 100 : 0;

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-xl font-bold text-slate-100 flex items-center space-x-2">
            <History className="w-5 h-5 text-amber-400" />
            <span>Paper Trade Journal</span>
          </h2>
          <p className="text-xs text-slate-400">Audited execution ledger with fees and slippage breakdown</p>
        </div>

        <button
          onClick={exportCSV}
          disabled={trades.length === 0}
          className="flex items-center space-x-2 px-3 py-1.5 rounded bg-slate-800 hover:bg-slate-700 border border-slate-700 text-xs font-semibold text-slate-200 transition-colors disabled:opacity-50"
        >
          <Download className="w-4 h-4" />
          <span>Export CSV</span>
        </button>
      </div>

      {/* Aggregate Journal Summary */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="p-4 rounded-xl bg-slate-900 border border-slate-800">
          <div className="text-xs text-slate-400 mb-1">Total Closed Trades</div>
          <div className="text-2xl font-bold font-mono text-slate-100">{trades.length}</div>
        </div>
        <div className="p-4 rounded-xl bg-slate-900 border border-slate-800">
          <div className="text-xs text-slate-400 mb-1">Win Rate</div>
          <div className="text-2xl font-bold font-mono text-amber-400">{winRate.toFixed(1)}%</div>
          <div className="text-xs text-slate-500 mt-1">{winCount} Wins / {trades.length - winCount} Losses</div>
        </div>
        <div className="p-4 rounded-xl bg-slate-900 border border-slate-800">
          <div className="text-xs text-slate-400 mb-1">Net Realized P&L</div>
          <div className={`text-2xl font-bold font-mono ${totalPnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
            {totalPnl >= 0 ? '+' : ''}${totalPnl.toFixed(2)}
          </div>
        </div>
      </div>

      {/* Trades Table */}
      <div className="p-4 rounded-xl bg-slate-900 border border-slate-800 overflow-x-auto">
        {trades.length === 0 ? (
          <div className="p-8 text-center text-xs text-slate-500">
            No completed paper trades recorded in current session.
          </div>
        ) : (
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-slate-800 text-slate-400">
                <th className="pb-3 font-semibold">Side</th>
                <th className="pb-3 font-semibold">Symbol</th>
                <th className="pb-3 font-semibold">Quantity</th>
                <th className="pb-3 font-semibold">Entry Price</th>
                <th className="pb-3 font-semibold">Exit Price</th>
                <th className="pb-3 font-semibold">Fee</th>
                <th className="pb-3 font-semibold">Slippage</th>
                <th className="pb-3 font-semibold">Duration / Times</th>
                <th className="pb-3 font-semibold text-right">Net P&L</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60 font-mono">
              {trades.map((t) => (
                <tr key={t.trade_id} className="hover:bg-slate-800/30">
                  <td className="py-3">
                    <span
                      className={`px-2 py-0.5 rounded text-[11px] font-bold ${
                        t.side === 'LONG'
                          ? 'bg-emerald-500/20 text-emerald-400'
                          : 'bg-rose-500/20 text-rose-400'
                      }`}
                    >
                      {t.side}
                    </span>
                  </td>
                  <td className="py-3 text-slate-200">{t.symbol}</td>
                  <td className="py-3 text-slate-300">{t.quantity}</td>
                  <td className="py-3 text-slate-300">${t.entry_price.toFixed(2)}</td>
                  <td className="py-3 text-slate-300">${t.exit_price.toFixed(2)}</td>
                  <td className="py-3 text-slate-400">${t.fee.toFixed(2)}</td>
                  <td className="py-3 text-slate-400">${t.slippage.toFixed(2)}</td>
                  <td className="py-3 text-slate-500 text-[11px]">
                    <div>In: {t.entry_time.slice(11, 19)}</div>
                    <div>Out: {t.exit_time.slice(11, 19)}</div>
                  </td>
                  <td className="py-3 text-right font-bold">
                    <span className={t.pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}>
                      {t.pnl >= 0 ? '+' : ''}${t.pnl.toFixed(2)}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
};

