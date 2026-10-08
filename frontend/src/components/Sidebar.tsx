import React from 'react';
import {
  LayoutDashboard,
  CandlestickChart,
  GitBranch,
  Layers,
  History,
  ShieldCheck,
  Scale,
} from 'lucide-react';

export type TabId =
  | 'overview'
  | 'chart'
  | 'strategies'
  | 'positions'
  | 'journal'
  | 'risk'
  | 'parity';

interface SidebarProps {
  currentTab: TabId;
  onSelectTab: (tab: TabId) => void;
}

export const Sidebar: React.FC<SidebarProps> = ({ currentTab, onSelectTab }) => {
  const items: { id: TabId; label: string; icon: React.ReactNode }[] = [
    { id: 'overview', label: 'Overview', icon: <LayoutDashboard className="w-4 h-4" /> },
    { id: 'chart', label: 'Live Charts & Signals', icon: <CandlestickChart className="w-4 h-4" /> },
    { id: 'strategies', label: 'Strategy Monitor', icon: <GitBranch className="w-4 h-4" /> },
    { id: 'positions', label: 'Positions & Orders', icon: <Layers className="w-4 h-4" /> },
    { id: 'journal', label: 'Paper Journal', icon: <History className="w-4 h-4" /> },
    { id: 'risk', label: 'Risk & Circuit Breakers', icon: <ShieldCheck className="w-4 h-4" /> },
    { id: 'parity', label: 'Parity Analytics', icon: <Scale className="w-4 h-4" /> },
  ];

  return (
    <aside className="w-64 bg-slate-900/60 border-r border-slate-800 p-4 flex flex-col justify-between shrink-0">
      <div className="space-y-1.5">
        <div className="text-[10px] font-bold uppercase tracking-wider text-slate-500 px-3 pb-2">
          Platform Navigation
        </div>
        {items.map((item) => {
          const active = currentTab === item.id;
          return (
            <button
              key={item.id}
              onClick={() => onSelectTab(item.id)}
              className={`w-full flex items-center space-x-3 px-3 py-2.5 rounded-lg text-xs font-medium transition-colors text-left ${
                active
                  ? 'bg-amber-500/15 text-amber-400 border border-amber-500/30'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/60'
              }`}
            >
              <span className={active ? 'text-amber-400' : 'text-slate-400'}>{item.icon}</span>
              <span>{item.label}</span>
            </button>
          );
        })}
      </div>

      <div className="p-3 rounded-lg bg-slate-950/60 border border-slate-800/80 text-[11px] text-slate-400 space-y-1">
        <div className="text-slate-300 font-semibold">Engine Invariants</div>
        <div className="flex justify-between">
          <span>Execution:</span>
          <span className="font-mono text-amber-400">Next-Bar Open</span>
        </div>
        <div className="flex justify-between">
          <span>Instrument:</span>
          <span className="font-mono text-slate-200">PAXGUSDT</span>
        </div>
        <div className="flex justify-between">
          <span>Live Trading:</span>
          <span className="font-mono text-rose-400">DISABLED (P3)</span>
        </div>
      </div>
    </aside>
  );
};

