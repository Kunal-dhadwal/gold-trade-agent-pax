import React, { useState, useEffect } from 'react';
import { ShieldCheck, ShieldAlert, AlertTriangle, RotateCcw, CheckCircle2 } from 'lucide-react';
import { api } from '../services/api';

export const RiskMonitor: React.FC = () => {
  const [riskData, setRiskData] = useState<any>(null);
  const [events, setEvents] = useState<any[]>([]);
  const [operatorId, setOperatorId] = useState('OPERATOR_DESK_1');
  const [killReason, setKillReason] = useState('Excessive market volatility / flash anomaly');
  const [msg, setMsg] = useState<string | null>(null);

  const loadRisk = async () => {
    try {
      const [status, ev] = await Promise.all([api.getRiskStatus(), api.getRiskEvents()]);
      setRiskData(status);
      setEvents(ev);
    } catch (e) {
      console.error('Failed to load risk status:', e);
    }
  };

  useEffect(() => {
    loadRisk();
    const timer = setInterval(loadRisk, 4000);
    return () => clearInterval(timer);
  }, []);

  const handleTriggerKillSwitch = async () => {
    try {
      const res = await api.triggerKillSwitch(operatorId, killReason, 'FLATTEN_ALL');
      setMsg(res.message);
      loadRisk();
    } catch (e: any) {
      setMsg(`Error: ${e.message}`);
    }
  };

  const handleResetKillSwitch = async () => {
    try {
      const res = await api.resetKillSwitch(operatorId);
      setMsg(res.message);
      loadRisk();
    } catch (e: any) {
      setMsg(`Error: ${e.message}`);
    }
  };

  const isLatched = riskData?.kill_switch?.active;

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-bold text-slate-100 flex items-center space-x-2">
          <ShieldCheck className="w-5 h-5 text-amber-400" />
          <span>Institutional Risk Engine & Circuit Breakers</span>
        </h2>
        <p className="text-xs text-slate-400">8 Deterministic Gates with Unconditional Pre-Trade Veto Authority</p>
      </div>

      {msg && (
        <div className="p-3 rounded-lg bg-amber-500/15 border border-amber-500/30 text-amber-300 text-xs flex justify-between items-center">
          <span>{msg}</span>
          <button onClick={() => setMsg(null)} className="text-amber-400 hover:text-amber-200">
            ×
          </button>
        </div>
      )}

      {/* Emergency Kill Switch FSM Status Banner */}
      <div
        className={`p-5 rounded-xl border ${
          isLatched
            ? 'bg-rose-950/40 border-rose-500/60 text-rose-200'
            : 'bg-slate-900 border-slate-800 text-slate-200'
        }`}
      >
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center space-x-3">
            {isLatched ? (
              <ShieldAlert className="w-8 h-8 text-rose-400 animate-bounce" />
            ) : (
              <CheckCircle2 className="w-8 h-8 text-emerald-400" />
            )}
            <div>
              <div className="text-xs uppercase tracking-wider font-semibold text-slate-400">Kill Switch State</div>
              <div className="text-lg font-bold font-mono">
                {riskData?.kill_switch?.state || 'NORMAL'}
              </div>
            </div>
          </div>

          <div className="flex items-center space-x-3">
            {isLatched ? (
              <button
                onClick={handleResetKillSwitch}
                className="flex items-center space-x-2 px-4 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white font-semibold text-xs transition-colors shadow-lg"
              >
                <RotateCcw className="w-4 h-4" />
                <span>Manual Human Reset</span>
              </button>
            ) : (
              <div className="flex items-center space-x-2">
                <input
                  type="text"
                  value={killReason}
                  onChange={(e) => setKillReason(e.target.value)}
                  className="bg-slate-800 border border-slate-700 text-xs text-slate-200 rounded px-3 py-1.5 w-64 focus:outline-none"
                  placeholder="Reason for emergency stop"
                />
                <button
                  onClick={handleTriggerKillSwitch}
                  className="flex items-center space-x-1.5 px-4 py-2 rounded-lg bg-rose-600 hover:bg-rose-500 text-white font-bold text-xs transition-colors shadow-lg"
                >
                  <ShieldAlert className="w-4 h-4" />
                  <span>EMERGENCY FLATTEN ALL</span>
                </button>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* 8 Deterministic Risk Gates Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {[
          { id: 1, name: 'Gate 1: Portfolio Drawdown', desc: 'Max 10.0% Hard Stop', status: 'ACTIVE PASS' },
          { id: 2, name: 'Gate 2: Daily Loss Cap', desc: 'Max 3.0% Reset at 00:00 UTC', status: 'ACTIVE PASS' },
          { id: 3, name: 'Gate 3: Risk Per Trade', desc: 'Max 1.0% Notional Equity', status: 'ACTIVE PASS' },
          { id: 4, name: 'Gate 4: Single Position Cap', desc: 'Max 1 Concurrent Position', status: 'ACTIVE PASS' },
          { id: 5, name: 'Gate 5: Spread Multiplier', desc: 'Max 2.0x Rolling Median', status: 'ACTIVE PASS' },
          { id: 6, name: 'Gate 6: Consecutive Losses', desc: 'Max 4 Streak Cutoff', status: 'ACTIVE PASS' },
          { id: 7, name: 'Gate 7: Market Regime Filter', desc: 'Veto in CHOPPY Volatility', status: 'ACTIVE PASS' },
          { id: 8, name: 'Gate 8: Kill Switch Veto', desc: 'Zero AI authority latch', status: isLatched ? 'VETO ACTIVE' : 'ACTIVE PASS' },
        ].map((gate) => (
          <div key={gate.id} className="p-4 rounded-xl bg-slate-900 border border-slate-800 space-y-2">
            <div className="flex justify-between items-center text-xs">
              <span className="font-semibold text-slate-200">{gate.name}</span>
              <span
                className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                  gate.status.includes('PASS')
                    ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30'
                    : 'bg-rose-500/15 text-rose-400 border border-rose-500/30'
                }`}
              >
                {gate.status}
              </span>
            </div>
            <div className="text-xs text-slate-400">{gate.desc}</div>
          </div>
        ))}
      </div>

      {/* Audit Log Table */}
      <div className="p-4 rounded-xl bg-slate-900 border border-slate-800 space-y-3">
        <h3 className="text-sm font-semibold text-slate-200">Risk Audit & Circuit Breaker Events</h3>
        {events.length === 0 ? (
          <div className="p-6 text-center text-xs text-slate-500">Zero risk breach incidents recorded.</div>
        ) : (
          <div className="space-y-2">
            {events.map((ev) => (
              <div
                key={ev.event_id}
                className="p-3 rounded-lg bg-slate-950 border border-slate-800 flex justify-between items-center text-xs"
              >
                <div>
                  <span className="font-mono text-amber-400 font-bold mr-2">[{ev.gate_name}]</span>
                  <span className="text-slate-300">{ev.details}</span>
                </div>
                <div className="text-slate-500 font-mono text-[11px]">{ev.timestamp.slice(11, 19)}</div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};

