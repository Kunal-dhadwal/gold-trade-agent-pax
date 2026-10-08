"""In-memory Asynchronous Validation Job Manager (Zero Redis Dependency)."""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, Optional
from uuid import uuid4

from src.core.logger import logger


class JobStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass
class ValidationJob:
    """State and execution container for an asynchronous validation task."""
    job_id: str
    job_type: str
    status: JobStatus = JobStatus.PENDING
    progress_pct: float = 0.0
    stage: str = "SUBMITTED"
    error: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "job_type": self.job_type,
            "status": self.status.value,
            "progress_pct": round(self.progress_pct, 1),
            "stage": self.stage,
            "error": self.error,
            "created_at": self.created_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


class ValidationJobManager:
    """Manages asynchronous background validation jobs without requiring Redis."""

    def __init__(self):
        self._jobs: Dict[str, ValidationJob] = {}

    def create_job(self, job_type: str) -> ValidationJob:
        """Register a new job in PENDING state."""
        job_id = str(uuid4())
        job = ValidationJob(job_id=job_id, job_type=job_type)
        self._jobs[job_id] = job
        return job

    def get_job(self, job_id: str) -> Optional[ValidationJob]:
        """Fetch job state by ID."""
        return self._jobs.get(job_id)

    def update_progress(self, job_id: str, progress_pct: float, stage: str) -> None:
        """Update job progress."""
        job = self._jobs.get(job_id)
        if job:
            job.progress_pct = max(0.0, min(100.0, progress_pct))
            job.stage = stage

    async def run_async_task(
        self,
        job_id: str,
        coro_or_func: Callable[..., Any],
        *args,
        **kwargs,
    ) -> None:
        """Execute task in background and track progress/completion."""
        job = self._jobs.get(job_id)
        if not job:
            return

        job.status = JobStatus.RUNNING
        job.stage = "EXECUTING"
        job.progress_pct = 10.0

        try:
            if asyncio.iscoroutinefunction(coro_or_func):
                result = await coro_or_func(*args, **kwargs)
            else:
                # Run CPU bound function in executor
                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(None, lambda: coro_or_func(*args, **kwargs))

            job.status = JobStatus.COMPLETED
            job.progress_pct = 100.0
            job.stage = "FINISHED"
            job.result = result if isinstance(result, dict) else (result.to_dict() if hasattr(result, "to_dict") else {"result": str(result)})
            job.completed_at = datetime.now(timezone.utc)
            logger.info(f"Validation job {job_id} COMPLETED successfully.")

        except Exception as err:
            logger.error(f"Validation job {job_id} FAILED: {err}", exc_info=True)
            job.status = JobStatus.FAILED
            job.error = str(err)
            job.stage = "ERROR"
            job.completed_at = datetime.now(timezone.utc)


# Singleton job manager instance
job_manager = ValidationJobManager()

