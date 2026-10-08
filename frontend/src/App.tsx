import React, { useState, useEffect } from 'react';
import { Header } from './components/Header';
import { Sidebar, TabId } from './components/Sidebar';
import { Overview } from './pages/Overview';
import { LiveChart } from './pages/LiveChart';
import { Strategies } from './pages/Strategies';
import { PositionsOrders } from './pages/PositionsOrders';
import { TradeJournal } from './pages/TradeJournal';
import { RiskMonitor } from './pages/RiskMonitor';
import { ParityAnalytics } from './pages/ParityAnalytics';
import { wsClient } from './services/websocket';
import { api } from './services/api';

export const App: React.FC = () => {
  const [currentTab, setCurrentTab] = useState<TabId>('overview');
  const [selectedAccount, setSelectedAccount] = useState<string>('PAPER_ACC_DEFAULT');
  const [accounts, setAccounts] = useState<string[]>(['PAPER_ACC_DEFAULT']);
  const [killModalOpen, setKillModalOpen] = useState(false);
  const [killReason, setKillReason] = useState('Operator emergency stop');

  useEffect(() => {
    // Connect WebSocket stream
    wsClient.connect();

    // Fetch account list
    api
      .getAccounts()
      .then((accs) => {
        if (accs.length > 0) {
          const ids = accs.map((a) => a.account_id);
          setAccounts(ids);
          setSelectedAccount(ids[0]);
        }
      })
      .catch((e) => console.error('Failed to load accounts:', e));
  }, []);

  const handleTriggerKillSwitch = async () => {
    try {
      await api.triggerKillSwitch('HUMAN_OPERATOR_DESK', killReason, 'FLATTEN_ALL');
      setKillModalOpen(false);
      alert('KILL SWITCH ACTIVATED: All positions flattened and orders canceled.');
    } catch (e: any) {
      alert(`Error triggering Kill Switch: ${e.message}`);
    }
  };

  return (
    <div className="min-h-screen bg-slate-950 flex flex-col text-slate-100">
      <Header
        onTriggerKillSwitch={() => setKillModalOpen(true)}
        selectedAccount={selectedAccount}
        onSelectAccount={setSelectedAccount}
        accounts={accounts}
      />

      <div className="flex flex-1 overflow-hidden">
        <Sidebar currentTab={currentTab} onSelectTab={setCurrentTab} />

        <main className="flex-1 overflow-y-auto p-6 max-w-7xl mx-auto w-full">
          {currentTab === 'overview' && <Overview accountId={selectedAccount} />}
          {currentTab === 'chart' && <LiveChart />}
          {currentTab === 'strategies' && <Strategies accountId={selectedAccount} />}
          {currentTab === 'positions' && <PositionsOrders accountId={selectedAccount} />}
          {currentTab === 'journal' && <TradeJournal />}
          {currentTab === 'risk' && <RiskMonitor />}
          {currentTab === 'parity' && <ParityAnalytics />}
        </main>
      </div>

      {/* Emergency Kill Switch Confirmation Modal */}
      {killModalOpen && (
        <div className="fixed inset-0 bg-black/80 flex items-center justify-center p-4 z-50">
          <div className="bg-slate-900 border border-rose-500/60 rounded-xl max-w-md w-full p-6 space-y-4 shadow-2xl">
            <h3 className="text-lg font-bold text-rose-400">EMERGENCY KILL SWITCH CONFIRMATION</h3>
            <p className="text-xs text-slate-300 leading-relaxed">
              Activating the Kill Switch will immediately cancel all pending orders and flatten any open positions
              via aggressive virtual market orders. Trading will be latched in blocked state until manual human reset.
            </p>
            <div className="space-y-1 text-xs">
              <label className="text-slate-400">Trigger Reason:</label>
              <input
                type="text"
                value={killReason}
                onChange={(e) => setKillReason(e.target.value)}
                className="w-full bg-slate-800 border border-slate-700 rounded p-2 text-slate-100 focus:outline-none"
              />
            </div>
            <div className="flex justify-end space-x-3 pt-3 border-t border-slate-800">
              <button
                onClick={() => setKillModalOpen(false)}
                className="px-4 py-2 rounded bg-slate-800 text-slate-300 text-xs font-semibold hover:bg-slate-700"
              >
                Cancel
              </button>
              <button
                onClick={handleTriggerKillSwitch}
                className="px-4 py-2 rounded bg-rose-600 text-white text-xs font-bold hover:bg-rose-500"
              >
                CONFIRM EMERGENCY STOP
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

