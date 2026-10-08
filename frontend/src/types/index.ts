export interface PaperAccount {
  account_id: string;
  currency: string;
  balance: number;
  equity: number;
  status: string;
  created_at: string;
}

export interface Portfolio {
  account_id: string;
  balance: number;
  equity: number;
  margin_used: number;
  margin_free: number;
  unrealized_pnl: number;
  realized_pnl: number;
  daily_pnl: number;
  peak_equity: number;
  drawdown_pct: number;
  updated_at: string;
}

export interface PaperStrategy {
  strategy_id: string;
  name: string;
  category: string;
  status: string;
  active_session_id?: string;
}

export interface PaperOrder {
  order_id: string;
  account_id: string;
  strategy_id: string;
  symbol: string;
  side: string;
  order_type: string;
  quantity: number;
  price: number;
  stop_loss?: number;
  take_profit?: number;
  status: string;
  filled_price?: number;
  filled_quantity: number;
  created_at: string;
}

export interface Position {
  position_id: string;
  account_id: string;
  strategy_id: string;
  symbol: string;
  side: string;
  entry_price: number;
  current_price: number;
  quantity: number;
  stop_loss?: number;
  take_profit?: number;
  unrealized_pnl: number;
  realized_pnl: number;
  status: string;
  mae_usd: number;
  mfe_usd: number;
  opened_at: string;
  closed_at?: string;
}

export interface PaperTrade {
  trade_id: string;
  account_id: string;
  strategy_id: string;
  symbol: string;
  side: string;
  entry_price: number;
  exit_price: number;
  quantity: number;
  pnl: number;
  fee: number;
  slippage: number;
  entry_time: string;
  exit_time: string;
}

export interface RiskEvent {
  event_id: string;
  timestamp: string;
  gate_name: string;
  severity: string;
  action: string;
  details: string;
}

export interface ParityDimension {
  name: string;
  dimension_index: number;
  passed: boolean;
  status: string;
  measured_value: number;
  threshold_value: number;
  unit: string;
  details?: string;
}

export interface ParityReport {
  strategy_id: string;
  overall_status: string;
  is_promotable: boolean;
  total_backtest_trades: number;
  total_paper_trades: number;
  slippage_drift_pct: number;
  pnl_tracking_error_pct: number;
  signal_congruence_pct: number;
  trade_count_deviation_pct: number;
  disqualification_reasons: string[];
  dimensions: ParityDimension[];
}

export interface Candle {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  spread?: number;
}
