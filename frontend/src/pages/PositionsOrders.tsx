import React, { useState, useEffect } from 'react';
import { Layers, ArrowDownUp, CheckCircle2, Clock } from 'lucide-react';
import { Position, PaperOrder } from '../types';
import { api } from '../services/api';

interface PositionsOrdersProps {
  accountId: string;
}

export const PositionsOrders: React.FC<PositionsOrdersProps> = ({ accountId }) => {
  const [positions, setPositions] = useState<Position[]>([]);
  const [orders, setOrders] = useState<PaperOrder[]>([]);
  const [loading, setLoading] = useState(true);

  const loadData = async () => {
    try {
      const [pos, ord] = await Promise.all([api.getPositions(accountId), api.getOrders(accountId)]);
      setPositions(pos);
      setOrders(ord);
    } catch (e) {
      console.error('Failed to load positions and orders:', e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
    const timer = setInterval(loadData, 3000);
    return () => clearInterval(timer);
  }, [accountId]);

  return (
    <div className="space-y-6">
      {/* Positions Section */}
      <div>
        <h2 className="text-xl font-bold text-slate-100 flex items-center space-x-2">
          <Layers className="w-5 h-5 text-amber-400" />
          <span>Active Positions</span>
        </h2>
        <p className="text-xs text-slate-400">Excursion monitoring with Maximum Adverse/Favorable Excursion</p>
      </div>

      <div className="p-4 rounded-xl bg-slate-900 border border-slate-800 overflow-x-auto">
        {positions.length === 0 ? (
          <div className="p-6 text-center text-xs text-slate-500">No open positions. Strategy is Flat.</div>
        ) : (
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-slate-800 text-slate-400">
                <th className="pb-3 font-semibold">Symbol</th>
                <th className="pb-3 font-semibold">Side</th>
                <th className="pb-3 font-semibold">Quantity</th>
                <th className="pb-3 font-semibold">Entry Price</th>
                <th className="pb-3 font-semibold">Mark Price</th>
                <th className="pb-3 font-semibold">Stop Loss</th>
                <th className="pb-3 font-semibold">Take Profit</th>
                <th className="pb-3 font-semibold">MAE / MFE</th>
                <th className="pb-3 font-semibold text-right">Unrealized P&L</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60 font-mono">
              {positions.map((pos) => (
                <tr key={pos.position_id} className="hover:bg-slate-800/30">
                  <td className="py-3 font-bold text-slate-200">{pos.symbol}</td>
                  <td className="py-3">
                    <span
                      className={`px-2 py-0.5 rounded text-[11px] font-bold ${
                        pos.side === 'LONG'
                          ? 'bg-emerald-500/20 text-emerald-400'
                          : 'bg-rose-500/20 text-rose-400'
                      }`}
                    >
                      {pos.side}
                    </span>
                  </td>
                  <td className="py-3 text-slate-300">{pos.quantity}</td>
                  <td className="py-3 text-slate-300">${pos.entry_price.toFixed(2)}</td>
                  <td className="py-3 text-amber-400 font-bold">${pos.current_price.toFixed(2)}</td>
                  <td className="py-3 text-rose-400">
                    {pos.stop_loss ? `$${pos.stop_loss.toFixed(2)}` : 'None'}
                  </td>
                  <td className="py-3 text-emerald-400">
                    {pos.take_profit ? `$${pos.take_profit.toFixed(2)}` : 'None'}
                  </td>
                  <td className="py-3 text-slate-400 text-[11px]">
                    <span className="text-rose-400">-${pos.mae_usd.toFixed(2)}</span> /{' '}
                    <span className="text-emerald-400">+${pos.mfe_usd.toFixed(2)}</span>
                  </td>
                  <td className="py-3 text-right font-bold">
                    <span className={pos.unrealized_pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}>
                      {pos.unrealized_pnl >= 0 ? '+' : ''}${pos.unrealized_pnl.toFixed(2)}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Orders Section */}
      <div>
        <h2 className="text-xl font-bold text-slate-100 flex items-center space-x-2">
          <ArrowDownUp className="w-5 h-5 text-indigo-400" />
          <span>Paper Orders</span>
        </h2>
        <p className="text-xs text-slate-400">Virtual Next-Bar Open execution state transitions</p>
      </div>

      <div className="p-4 rounded-xl bg-slate-900 border border-slate-800 overflow-x-auto">
        {orders.length === 0 ? (
          <div className="p-6 text-center text-xs text-slate-500">No active pending or filled orders.</div>
        ) : (
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-slate-800 text-slate-400">
                <th className="pb-3 font-semibold">Order ID</th>
                <th className="pb-3 font-semibold">Symbol</th>
                <th className="pb-3 font-semibold">Side</th>
                <th className="pb-3 font-semibold">Type</th>
                <th className="pb-3 font-semibold">Quantity</th>
                <th className="pb-3 font-semibold">Price Requested</th>
                <th className="pb-3 font-semibold">Status</th>
                <th className="pb-3 font-semibold text-right">Created</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60 font-mono">
              {orders.map((ord) => (
                <tr key={ord.order_id} className="hover:bg-slate-800/30">
                  <td className="py-3 text-slate-400 text-[11px]">{ord.order_id}</td>
                  <td className="py-3 text-slate-200">{ord.symbol}</td>
                  <td className="py-3">
                    <span
                      className={`px-2 py-0.5 rounded text-[11px] font-bold ${
                        ord.side === 'LONG'
                          ? 'bg-emerald-500/20 text-emerald-400'
                          : 'bg-rose-500/20 text-rose-400'
                      }`}
                    >
                      {ord.side}
                    </span>
                  </td>
                  <td className="py-3 text-slate-400">{ord.order_type}</td>
                  <td className="py-3 text-slate-300">{ord.quantity}</td>
                  <td className="py-3 text-slate-300">${ord.price.toFixed(2)}</td>
                  <td className="py-3">
                    <span className="px-2 py-0.5 rounded text-[11px] bg-slate-800 text-slate-200 border border-slate-700">
                      {ord.status}
                    </span>
                  </td>
                  <td className="py-3 text-right text-slate-500 text-[11px]">{ord.created_at}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
};

