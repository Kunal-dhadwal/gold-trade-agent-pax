import React, { useState, useEffect } from 'react';
import { ShieldAlert, Activity, Wifi, WifiOff } from 'lucide-react';
import { wsClient } from '../services/websocket';
import { api } from '../services/api';

interface HeaderProps {
  onTriggerKillSwitch: () => void;
  selectedAccount: string;
  onSelectAccount: (acc: string) => void;
  accounts: string[];
}

export const Header: React.FC<HeaderProps> = ({
  onTriggerKillSwitch,
  selectedAccount,
  onSelectAccount,
  accounts,
}) => {
  const [isConnected, setIsConnected] = useState(false);

  useEffect(() => {
    const unsub = wsClient.on('connection_status', (data) => {
      setIsConnected(data.status === 'CONNECTED');
    });
    return () => {
      unsub();
    };
  }, []);

  return (
    <header className="bg-slate-900 border-b border-slate-800 px-6 py-3 flex flex-wrap items-center justify-between gap-4 sticky top-0 z-50">
      <div className="flex items-center space-x-4">
        <div className="flex items-center space-x-2">
          <div className="w-8 h-8 rounded-lg bg-amber-500/20 border border-amber-500/40 flex items-center justify-center font-bold text-amber-400">
            Au
          </div>
          <div>
            <h1 className="text-base font-bold text-slate-100 leading-tight">AGentTrXPAUS</h1>
            <p className="text-xs text-slate-400">Gold AI Quantitative Trading</p>
          </div>
        </div>

        {/* MANDATORY SAFETY TENET BANNER */}
        <div className="px-3 py-1 rounded bg-amber-500/10 border border-amber-500/30 text-amber-400 font-mono text-xs font-semibold tracking-wider flex items-center space-x-2">
          <span className="w-2 h-2 rounded-full bg-amber-400 animate-pulse"></span>
          <span>[PAPER TRADING MODE — ZERO REAL CAPITAL AT RISK]</span>
        </div>
      </div>

      <div className="flex items-center space-x-4">
        {/* Account Selector */}
        <div className="flex items-center space-x-2 text-xs">
          <span className="text-slate-400">Account:</span>
          <select
            value={selectedAccount}
            onChange={(e) => onSelectAccount(e.target.value)}
            className="bg-slate-800 border border-slate-700 text-slate-200 rounded px-2.5 py-1 text-xs focus:outline-none focus:border-amber-500"
          >
            {accounts.map((acc) => (
              <option key={acc} value={acc}>
                {acc}
              </option>
            ))}
          </select>
        </div>

        {/* WebSocket Stream Indicator */}
        <div className="flex items-center space-x-1.5 px-2.5 py-1 rounded bg-slate-800/80 border border-slate-700/60 text-xs font-mono">
          {isConnected ? (
            <>
              <Wifi className="w-3.5 h-3.5 text-emerald-400" />
              <span className="text-emerald-400">FEED LIVE</span>
            </>
          ) : (
            <>
              <WifiOff className="w-3.5 h-3.5 text-rose-400" />
              <span className="text-rose-400">DISCONNECTED</span>
            </>
          )}
        </div>

        {/* Emergency Kill Switch Button */}
        <button
          onClick={onTriggerKillSwitch}
          className="flex items-center space-x-1.5 px-3 py-1.5 rounded bg-rose-600/20 hover:bg-rose-600/30 border border-rose-500/40 text-rose-300 font-semibold text-xs transition-colors shadow-sm"
          title="Emergency Flatten All Positions & Cancel Orders"
        >
          <ShieldAlert className="w-4 h-4 text-rose-400" />
          <span>KILL SWITCH</span>
        </button>
      </div>
    </header>
  );
};

