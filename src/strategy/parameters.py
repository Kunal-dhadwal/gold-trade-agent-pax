"""Strategy parameter specification and validation schema."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ParameterSpec(BaseModel):
    name: str
    param_type: str = "float"  # int, float, bool, str
    default: Any
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    step: Optional[float] = None
    description: str = ""

    def validate_value(self, val: Any) -> Any:
        if self.param_type == "int":
            v = int(val)
            if self.min_value is not None and v < self.min_value:
                raise ValueError(f"{self.name} value {v} below min {self.min_value}")
            if self.max_value is not None and v > self.max_value:
                raise ValueError(f"{self.name} value {v} above max {self.max_value}")
            return v
        elif self.param_type == "float":
            v = float(val)
            if self.min_value is not None and v < self.min_value:
                raise ValueError(f"{self.name} value {v} below min {self.min_value}")
            if self.max_value is not None and v > self.max_value:
                raise ValueError(f"{self.name} value {v} above max {self.max_value}")
            return v
        elif self.param_type == "bool":
            return bool(val)
        return str(val)
