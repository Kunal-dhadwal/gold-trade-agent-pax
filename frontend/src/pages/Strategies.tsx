import React, { useState, useEffect } from 'react';
import { Play, Pause, Square, CheckCircle, AlertCircle } from 'lucide-react';
import { PaperStrategy } from '../types';
import { api } from '../services/api';

interface StrategiesProps {
  accountId: string;
}

export const Strategies: React.FC<StrategiesProps> = ({ accountId }) => {
  const [strategies, setStrategies] = useState<PaperStrategy[]>([]);
  const [loading, setLoading] = useState(true);
  const [actionMessage, setActionMessage] = useState<string | null>(null);

  const loadStrategies = async () => {
    try {
      const data = await api.getStrategies();
      setStrategies(data);
    } catch (e) {
      console.error('Failed to load strategies:', e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadStrategies();
  }, []);

  const handleStart = async (stratId: string) => {
    try {
      await api.startStrategy(stratId, accountId);
      setActionMessage(`Strategy ${stratId} activated in paper mode.`);
      loadStrategies();
    } catch (e: any) {
      setActionMessage(`Error starting: ${e.message}`);
    }
  };

  const handlePause = async (stratId: string) => {
    try {
      await api.pauseStrategy(stratId, accountId);
      setActionMessage(`Strategy ${stratId} paused. New entries blocked.`);
      loadStrategies();
    } catch (e: any) {
      setActionMessage(`Error pausing: ${e.message}`);
    }
  };

  const handleStop = async (stratId: string) => {
    try {
      await api.stopStrategy(stratId, accountId);
      setActionMessage(`Strategy ${stratId} stopped and liquidated.`);
      loadStrategies();
    } catch (e: any) {
      setActionMessage(`Error stopping: ${e.message}`);
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-bold text-slate-100">Single Active Strategy Monitor</h2>
        <p className="text-xs text-slate-400">
          Enforces Decision B: Exactly one active strategy execution loop per paper account
        </p>
      </div>

      {actionMessage && (
        <div className="p-3 rounded-lg bg-amber-500/15 border border-amber-500/30 text-amber-300 text-xs flex justify-between items-center">
          <span>{actionMessage}</span>
          <button onClick={() => setActionMessage(null)} className="text-amber-400 hover:text-amber-200">
            ×
          </button>
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {strategies.map((strat) => (
          <div key={strat.strategy_id} className="p-5 rounded-xl bg-slate-900 border border-slate-800 space-y-4">
            <div className="flex justify-between items-start">
              <div>
                <h3 className="font-bold text-slate-100 text-base">{strat.name}</h3>
                <div className="text-xs text-slate-400 font-mono">{strat.strategy_id}</div>
              </div>
              <span className="px-2.5 py-1 rounded bg-indigo-500/15 border border-indigo-500/30 text-indigo-400 text-xs font-semibold">
                {strat.category}
              </span>
            </div>

            <div className="p-3 rounded-lg bg-slate-950/60 border border-slate-800 text-xs text-slate-400 space-y-1 font-mono">
              <div className="flex justify-between">
                <span>Execution Status:</span>
                <span className="text-emerald-400 font-bold">{strat.status}</span>
              </div>
              <div className="flex justify-between">
                <span>Active Session:</span>
                <span className="text-slate-300">{strat.active_session_id || 'None'}</span>
              </div>
              <div className="flex justify-between">
                <span>Account Binding:</span>
                <span className="text-slate-300">{accountId}</span>
              </div>
            </div>

            {/* Operator Control Actions */}
            <div className="flex items-center space-x-2 pt-2 border-t border-slate-800">
              <button
                onClick={() => handleStart(strat.strategy_id)}
                className="flex-1 flex items-center justify-center space-x-1.5 px-3 py-2 rounded bg-emerald-600/20 hover:bg-emerald-600/30 border border-emerald-500/40 text-emerald-400 text-xs font-semibold transition-colors"
              >
                <Play className="w-3.5 h-3.5" />
                <span>Start</span>
              </button>

              <button
                onClick={() => handlePause(strat.strategy_id)}
                className="flex-1 flex items-center justify-center space-x-1.5 px-3 py-2 rounded bg-amber-600/20 hover:bg-amber-600/30 border border-amber-500/40 text-amber-400 text-xs font-semibold transition-colors"
              >
                <Pause className="w-3.5 h-3.5" />
                <span>Pause</span>
              </button>

              <button
                onClick={() => handleStop(strat.strategy_id)}
                className="flex-1 flex items-center justify-center space-x-1.5 px-3 py-2 rounded bg-rose-600/20 hover:bg-rose-600/30 border border-rose-500/40 text-rose-400 text-xs font-semibold transition-colors"
              >
                <Square className="w-3.5 h-3.5" />
                <span>Stop</span>
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

