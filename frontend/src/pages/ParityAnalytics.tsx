import React, { useState, useEffect } from 'react';
import { Scale, CheckCircle2, XCircle, AlertTriangle, KeyRound, ShieldAlert } from 'lucide-react';
import { ParityReport } from '../types';
import { api } from '../services/api';

export const ParityAnalytics: React.FC = () => {
  const [report, setReport] = useState<ParityReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [strategyId, setStrategyId] = useState('gold_ema_trend_cross');
  const [operatorId, setOperatorId] = useState('HUMAN_LEAD_OPERATOR_1');
  const [operatorSecret, setOperatorSecret] = useState('');
  const [signOffResult, setSignOffResult] = useState<any>(null);
  const [evalMsg, setEvalMsg] = useState<string | null>(null);

  const loadParity = async () => {
    try {
      const data = await api.getParityReport(strategyId);
      setReport(data);
    } catch (e) {
      console.error('Failed to load parity report:', e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadParity();
  }, [strategyId]);

  const handleEvaluate = async () => {
    try {
      const res = await api.evaluatePromotion(strategyId);
      setEvalMsg(
        res.eligible_for_live_candidate
          ? 'SUCCESS: Strategy meets all Phase 2 + Phase 3 promotion gates! Ready for human sign-off.'
          : `DISQUALIFIED: ${res.disqualifications.join('; ')}`
      );
      loadParity();
    } catch (e: any) {
      setEvalMsg(`Evaluation error: ${e.message}`);
    }
  };

  const handleSignOff = async () => {
    if (!operatorSecret) {
      setEvalMsg('Please enter operator cryptographic authorization key.');
      return;
    }
    try {
      const res = await api.signOffPromotion(strategyId, operatorId, operatorSecret);
      setSignOffResult(res);
      setEvalMsg(res.message);
    } catch (e: any) {
      setEvalMsg(`Sign-off rejected: ${e.message}`);
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-bold text-slate-100 flex items-center space-x-2">
          <Scale className="w-5 h-5 text-amber-400" />
          <span>Backtest ↔ Paper Parity Analytics & Promotion Gate</span>
        </h2>
        <p className="text-xs text-slate-400">
          Evaluates 11 canonical dimensions of execution fidelity (Section 20 & 19 of Specification)
        </p>
      </div>

      {evalMsg && (
        <div className="p-3 rounded-lg bg-amber-500/15 border border-amber-500/30 text-amber-300 text-xs flex justify-between items-center">
          <span>{evalMsg}</span>
          <button onClick={() => setEvalMsg(null)} className="text-amber-400 hover:text-amber-200">
            ×
          </button>
        </div>
      )}

      {/* Parity Status Header Card */}
      <div className="p-5 rounded-xl bg-slate-900 border border-slate-800 flex flex-wrap items-center justify-between gap-4">
        <div>
          <div className="text-xs text-slate-400">Overall Parity Classification</div>
          <div className="flex items-center space-x-2 mt-1">
            <span
              className={`px-3 py-1 rounded-full text-xs font-bold font-mono ${
                report?.overall_status === 'PARITY_PASS'
                  ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/40'
                  : report?.overall_status === 'PARITY_WARNING'
                  ? 'bg-amber-500/20 text-amber-400 border border-amber-500/40'
                  : 'bg-rose-500/20 text-rose-400 border border-rose-500/40'
              }`}
            >
              {report?.overall_status || 'PARITY_PASS'}
            </span>
            <span className="text-xs text-slate-400">
              {report?.is_promotable ? 'Eligible for Promotion Evaluation' : 'Disqualified from Promotion'}
            </span>
          </div>
        </div>

        <div className="flex items-center space-x-6 text-xs font-mono">
          <div>
            <div className="text-slate-500">P&L Tracking Error</div>
            <div className="text-slate-200 font-bold">{report?.pnl_tracking_error_pct.toFixed(2)}% (Max 2.0%)</div>
          </div>
          <div>
            <div className="text-slate-500">Slippage Drift</div>
            <div className="text-slate-200 font-bold">{report?.slippage_drift_pct.toFixed(2)}% (Max 20.0%)</div>
          </div>
          <div>
            <div className="text-slate-500">Signal Congruence</div>
            <div className="text-emerald-400 font-bold">{report?.signal_congruence_pct.toFixed(1)}% (100% Req)</div>
          </div>
        </div>
      </div>

      {/* 11 Canonical Parity Dimensions Table */}
      <div className="p-4 rounded-xl bg-slate-900 border border-slate-800 space-y-3">
        <h3 className="text-sm font-semibold text-slate-200">11-Dimension Execution Congruence Checklist</h3>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-slate-800 text-slate-400">
                <th className="pb-3 font-semibold">Dim #</th>
                <th className="pb-3 font-semibold">Dimension Name</th>
                <th className="pb-3 font-semibold">Status</th>
                <th className="pb-3 font-semibold">Measured Value</th>
                <th className="pb-3 font-semibold">Threshold Target</th>
                <th className="pb-3 font-semibold text-right">Fidelity Verification</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60 font-mono">
              {report?.dimensions.map((dim) => (
                <tr key={dim.dimension_index} className="hover:bg-slate-800/30">
                  <td className="py-2.5 text-slate-500">D{dim.dimension_index}</td>
                  <td className="py-2.5 font-sans font-medium text-slate-200">{dim.name}</td>
                  <td className="py-2.5">
                    <span
                      className={`px-2 py-0.5 rounded text-[11px] font-bold ${
                        dim.status === 'PARITY_PASS'
                          ? 'bg-emerald-500/15 text-emerald-400'
                          : dim.status === 'PARITY_WARNING'
                          ? 'bg-amber-500/15 text-amber-400'
                          : 'bg-rose-500/15 text-rose-400'
                      }`}
                    >
                      {dim.status}
                    </span>
                  </td>
                  <td className="py-2.5 text-slate-200">
                    {dim.measured_value.toFixed(2)} {dim.unit}
                  </td>
                  <td className="py-2.5 text-slate-400">
                    {dim.threshold_value.toFixed(2)} {dim.unit}
                  </td>
                  <td className="py-2.5 text-right">
                    {dim.passed ? (
                      <span className="text-emerald-400 font-bold flex items-center justify-end space-x-1">
                        <CheckCircle2 className="w-3.5 h-3.5" />
                        <span>CONGRUENT</span>
                      </span>
                    ) : (
                      <span className="text-rose-400 font-bold flex items-center justify-end space-x-1">
                        <XCircle className="w-3.5 h-3.5" />
                        <span>DIVERGENCE</span>
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Dual-Stage Promotion & Cryptographic Sign-Off Section */}
      <div className="p-5 rounded-xl bg-slate-900 border border-slate-800 space-y-4">
        <div>
          <h3 className="text-sm font-semibold text-slate-200 flex items-center space-x-2">
            <KeyRound className="w-4 h-4 text-amber-400" />
            <span>Dual-Stage Paper-to-Live Promotion Certification</span>
          </h3>
          <p className="text-xs text-slate-400">
            Mandatory Human Operator cryptographic sign-off. AI agents possess ZERO promotion authority.
          </p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="p-4 rounded-lg bg-slate-950 border border-slate-800 text-xs text-slate-300 space-y-2">
            <div className="font-semibold text-amber-400">Stage 1: Phase 2 Research Gates</div>
            <div>• Minimum Trades: N ≥ 50</div>
            <div>• Walk-Forward Efficiency: WFE ≥ 50%</div>
            <div>• Monte Carlo Ruin Prob: P(DD ≥ 20%) &lt; 5.0%</div>
            <div>• Composite Score: CSS ≥ 80.0</div>
            <div>• Out-of-Sample Holdout: Sharpe ≥ 1.0</div>
          </div>

          <div className="p-4 rounded-lg bg-slate-950 border border-slate-800 text-xs text-slate-300 space-y-2">
            <div className="font-semibold text-amber-400">Stage 2: Phase 3 Realized Paper Gates</div>
            <div>• Continuous Duration: ≥ 4 continuous calendar weeks</div>
            <div>• Executed Paper Trades: N_paper ≥ 50</div>
            <div>• Realized Expectancy: &gt; 0.0 USD/trade</div>
            <div>• Gross Profit Factor: GPF ≥ 1.25</div>
            <div>• Realized Max Drawdown: MaxDD ≤ 12.0%</div>
            <div>• Parity Status: PARITY_PASS (Tracking error ≤ 2.0%)</div>
          </div>
        </div>

        {/* Action Controls */}
        <div className="flex flex-wrap items-center justify-between gap-4 pt-3 border-t border-slate-800">
          <button
            onClick={handleEvaluate}
            className="px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white font-semibold text-xs transition-colors"
          >
            Run Automated Dual-Stage Evaluation
          </button>

          <div className="flex items-center space-x-2">
            <input
              type="text"
              placeholder="Operator ID"
              value={operatorId}
              onChange={(e) => setOperatorId(e.target.value)}
              className="bg-slate-800 border border-slate-700 text-xs text-slate-200 rounded px-2.5 py-1.5 focus:outline-none"
            />
            <input
              type="password"
              placeholder="Operator Secret Key"
              value={operatorSecret}
              onChange={(e) => setOperatorSecret(e.target.value)}
              className="bg-slate-800 border border-slate-700 text-xs text-slate-200 rounded px-2.5 py-1.5 focus:outline-none w-48"
            />
            <button
              onClick={handleSignOff}
              className="px-4 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white font-bold text-xs transition-colors flex items-center space-x-1.5"
            >
              <KeyRound className="w-3.5 h-3.5" />
              <span>Human Operator Sign-Off</span>
            </button>
          </div>
        </div>

        {signOffResult && (
          <div className="p-3 rounded-lg bg-emerald-500/10 border border-emerald-500/30 text-xs font-mono text-emerald-400">
            <div>STATUS: {signOffResult.status}</div>
            <div>SIGNED BY: {signOffResult.signed_by}</div>
            <div>SIGNATURE: {signOffResult.signature}</div>
            <div>TIMESTAMP: {signOffResult.signed_at}</div>
          </div>
        )}
      </div>
    </div>
  );
};

