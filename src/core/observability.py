"""Observability, Prometheus Metrics, and Audit Logging for Phase 3 Platform."""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
import threading
import time
from typing import Any, Dict, List, Optional
import uuid


@dataclass
class MetricCounter:
    name: str
    description: str
    counts: Dict[str, float] = field(default_factory=lambda: defaultdict(float))

    def inc(self, value: float = 1.0, labels: Optional[Dict[str, str]] = None) -> None:
        key = self._format_labels(labels)
        self.counts[key] += value

    def _format_labels(self, labels: Optional[Dict[str, str]]) -> str:
        if not labels:
            return ""
        return "{" + ",".join(f'{k}="{v}"' for k, v in sorted(labels.items())) + "}"


@dataclass
class MetricGauge:
    name: str
    description: str
    values: Dict[str, float] = field(default_factory=lambda: defaultdict(float))

    def set(self, value: float, labels: Optional[Dict[str, str]] = None) -> None:
        key = self._format_labels(labels)
        self.values[key] = value

    def _format_labels(self, labels: Optional[Dict[str, str]]) -> str:
        if not labels:
            return ""
        return "{" + ",".join(f'{k}="{v}"' for k, v in sorted(labels.items())) + "}"


class MetricsRegistry:
    """Thread-safe lightweight in-memory Prometheus metrics registry."""

    _instance: Optional["MetricsRegistry"] = None
    _lock = threading.Lock()

    def __init__(self):
        self.paper_orders_total = MetricCounter(
            name="paper_orders_total",
            description="Total count of paper orders submitted and filled",
        )
        self.paper_slippage_usd = MetricCounter(
            name="paper_slippage_usd",
            description="Cumulative execution slippage in USD",
        )
        self.paper_account_equity = MetricGauge(
            name="paper_account_equity",
            description="Current real-time paper account equity in USD",
        )
        self.paper_account_drawdown_pct = MetricGauge(
            name="paper_account_drawdown_pct",
            description="Current peak-to-trough paper account drawdown percentage",
        )
        self.websocket_connected_clients_total = MetricGauge(
            name="websocket_connected_clients_total",
            description="Current number of active connected WebSocket dashboard clients",
        )

    @classmethod
    def get_instance(cls) -> "MetricsRegistry":
        with cls._lock:
            if cls._instance is None:
                cls._instance = MetricsRegistry()
            return cls._instance

    def format_prometheus_text(self) -> str:
        """Format metrics as standard Prometheus exposition format."""
        lines = []

        # paper_orders_total
        lines.append(f"# HELP {self.paper_orders_total.name} {self.paper_orders_total.description}")
        lines.append(f"# TYPE {self.paper_orders_total.name} counter")
        for lbls, val in self.paper_orders_total.counts.items():
            lines.append(f"{self.paper_orders_total.name}{lbls} {val}")

        # paper_slippage_usd
        lines.append(f"# HELP {self.paper_slippage_usd.name} {self.paper_slippage_usd.description}")
        lines.append(f"# TYPE {self.paper_slippage_usd.name} counter")
        for lbls, val in self.paper_slippage_usd.counts.items():
            lines.append(f"{self.paper_slippage_usd.name}{lbls} {val}")

        # paper_account_equity
        lines.append(f"# HELP {self.paper_account_equity.name} {self.paper_account_equity.description}")
        lines.append(f"# TYPE {self.paper_account_equity.name} gauge")
        for lbls, val in self.paper_account_equity.values.items():
            lines.append(f"{self.paper_account_equity.name}{lbls} {val}")

        # paper_account_drawdown_pct
        lines.append(f"# HELP {self.paper_account_drawdown_pct.name} {self.paper_account_drawdown_pct.description}")
        lines.append(f"# TYPE {self.paper_account_drawdown_pct.name} gauge")
        for lbls, val in self.paper_account_drawdown_pct.values.items():
            lines.append(f"{self.paper_account_drawdown_pct.name}{lbls} {val}")

        # websocket_connected_clients_total
        lines.append(f"# HELP {self.websocket_connected_clients_total.name} {self.websocket_connected_clients_total.description}")
        lines.append(f"# TYPE {self.websocket_connected_clients_total.name} gauge")
        for lbls, val in self.websocket_connected_clients_total.values.items():
            lines.append(f"{self.websocket_connected_clients_total.name}{lbls} {val}")

        return "\n".join(lines) + "\n"


metrics = MetricsRegistry.get_instance()


def generate_trace_id() -> str:
    """Generate 128-bit trace ID string for distributed request tracking."""
    return uuid.uuid4().hex


def generate_correlation_id() -> str:
    """Generate correlation ID string."""
    return f"corr_{uuid.uuid4().hex[:12]}"

