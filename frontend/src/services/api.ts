import {
  PaperAccount,
  Portfolio,
  PaperStrategy,
  PaperOrder,
  Position,
  PaperTrade,
  RiskEvent,
  ParityReport,
} from '../types';

const API_BASE = '/api/v1';

async function fetchJson<T>(url: string, options?: RequestInit): Promise<T> {
  const token = localStorage.getItem('jwt_token') || '';
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options?.headers as Record<string, string>),
  };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const res = await fetch(url, { ...options, headers });
  if (!res.ok) {
    const errorText = await res.text();
    throw new Error(`API Error ${res.status}: ${errorText}`);
  }
  return res.json();
}

export const api = {
  // Accounts & Portfolio
  getAccounts: () => fetchJson<PaperAccount[]>(`${API_BASE}/paper/accounts`),
  createAccount: (account_id: string, initial_balance: number) =>
    fetchJson<PaperAccount>(`${API_BASE}/paper/accounts`, {
      method: 'POST',
      body: JSON.stringify({ account_id, initial_balance }),
    }),
  getPortfolio: (accountId: string) =>
    fetchJson<Portfolio>(`${API_BASE}/paper/accounts/${accountId}/portfolio`),
  getEquityHistory: (accountId: string) =>
    fetchJson<{ timestamp: string; equity: number; balance: number; drawdown_pct: number }[]>(
      `${API_BASE}/paper/accounts/${accountId}/equity-history`
    ),

  // Strategies & Controls
  getStrategies: () => fetchJson<PaperStrategy[]>(`${API_BASE}/paper/strategies`),
  startStrategy: (strategyId: string, accountId: string) =>
    fetchJson(`${API_BASE}/paper/strategies/${strategyId}/start`, {
      method: 'POST',
      body: JSON.stringify({ strategy_id: strategyId, account_id: accountId }),
    }),
  pauseStrategy: (strategyId: string, accountId?: string) =>
    fetchJson(`${API_BASE}/paper/strategies/${strategyId}/pause?account_id=${accountId || ''}`, {
      method: 'POST',
    }),
  stopStrategy: (strategyId: string, accountId?: string) =>
    fetchJson(`${API_BASE}/paper/strategies/${strategyId}/stop?account_id=${accountId || ''}`, {
      method: 'POST',
    }),

  // Orders, Positions, Trades
  getOrders: (accountId?: string) =>
    fetchJson<PaperOrder[]>(`${API_BASE}/paper/orders${accountId ? `?account_id=${accountId}` : ''}`),
  getPositions: (accountId?: string) =>
    fetchJson<Position[]>(`${API_BASE}/paper/positions${accountId ? `?account_id=${accountId}` : ''}`),
  getTrades: (strategyId?: string) =>
    fetchJson<PaperTrade[]>(`${API_BASE}/paper/trades${strategyId ? `?strategy_id=${strategyId}` : ''}`),

  // Parity & Promotion
  getParityReport: (strategyId: string) =>
    fetchJson<ParityReport>(`${API_BASE}/paper/parity/${strategyId}`),
  evaluatePromotion: (strategyId: string) =>
    fetchJson(`${API_BASE}/paper/promotion/${strategyId}/evaluate`, { method: 'POST' }),
  signOffPromotion: (strategyId: string, operator_id: string, operator_secret: string) =>
    fetchJson(`${API_BASE}/paper/promotion/${strategyId}/sign-off`, {
      method: 'POST',
      body: JSON.stringify({ operator_id, operator_secret }),
    }),

  // Risk & Kill Switch
  getRiskStatus: () => fetchJson<any>(`${API_BASE}/risk/status`),
  getRiskEvents: (severity?: string) =>
    fetchJson<RiskEvent[]>(`${API_BASE}/risk/events${severity ? `?severity=${severity}` : ''}`),
  triggerKillSwitch: (operator_id: string, reason: string, policy: string = 'FLATTEN_ALL') =>
    fetchJson(`${API_BASE}/risk/kill-switch`, {
      method: 'POST',
      body: JSON.stringify({ operator_id, reason, policy }),
    }),
  resetKillSwitch: (operator_id: string) =>
    fetchJson(`${API_BASE}/risk/kill-switch/reset?operator_id=${encodeURIComponent(operator_id)}`, {
      method: 'POST',
    }),
};

