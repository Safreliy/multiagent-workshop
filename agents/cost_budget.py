"""Shared, fail-closed dollar budget for model requests.

The ledger deliberately accounts successful calls at configured price ceilings rather
than pretending those ceilings are provider billing data. Failed calls and calls with
missing usage retain their full pre-request reservation.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
from pathlib import Path
from typing import Any


class BudgetExceeded(RuntimeError):
    pass


def _money(value: float) -> float:
    return round(float(value), 12)


class CostBudget:
    """A process-wide, thread-safe budget shared by every matrix attempt."""

    VERSION = 1

    def __init__(self, max_cost_usd: float, path: Path) -> None:
        if not math.isfinite(max_cost_usd) or max_cost_usd <= 0:
            raise ValueError("max_cost_usd must be a positive finite number")
        self.max_cost_usd = float(max_cost_usd)
        self.path = Path(path)
        self._lock = threading.RLock()
        self._calls: dict[str, dict[str, Any]] = {}
        self._halted_reason: str | None = None
        self._created_at = time.time()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._write_locked()

    @property
    def reserved_usd(self) -> float:
        with self._lock:
            return sum(
                float(call["reserved_cost_usd"])
                for call in self._calls.values()
                if call["status"] == "reserved"
            )

    @property
    def spent_usd(self) -> float:
        with self._lock:
            return sum(
                float(call.get("accounted_cost_usd") or 0)
                for call in self._calls.values()
                if call["status"] == "settled"
            )

    @property
    def available_usd(self) -> float:
        with self._lock:
            if self._halted_reason is not None:
                return 0.0
            return max(0.0, self.max_cost_usd - self._committed_locked())

    def reserve(
        self,
        request_id: str,
        *,
        model: str,
        input_tokens_upper_bound: int,
        output_tokens_upper_bound: int,
        input_price_ceiling: float,
        output_price_ceiling: float,
    ) -> float:
        if input_tokens_upper_bound < 0 or output_tokens_upper_bound < 1:
            raise ValueError("token upper bounds must be non-negative with bounded output")
        for name, value in (
            ("input_price_ceiling", input_price_ceiling),
            ("output_price_ceiling", output_price_ceiling),
        ):
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be a non-negative finite number")
        reservation = (
            input_tokens_upper_bound * input_price_ceiling
            + output_tokens_upper_bound * output_price_ceiling
        ) / 1_000_000
        reservation = _money(reservation)
        with self._lock:
            if request_id in self._calls:
                raise RuntimeError(f"duplicate model request id: {request_id}")
            if self._halted_reason is not None:
                raise BudgetExceeded(f"USD budget halted: {self._halted_reason}")
            available = self.max_cost_usd - self._committed_locked()
            if reservation > available + 1e-12:
                self._calls[request_id] = {
                    "status": "denied",
                    "model": model,
                    "created_at": time.time(),
                    "input_tokens_upper_bound": input_tokens_upper_bound,
                    "output_tokens_upper_bound": output_tokens_upper_bound,
                    "input_price_ceiling_per_million": input_price_ceiling,
                    "output_price_ceiling_per_million": output_price_ceiling,
                    "requested_reservation_usd": reservation,
                    "available_usd": _money(max(0.0, available)),
                }
                self._write_locked()
                raise BudgetExceeded(
                    f"USD budget cannot reserve ${reservation:.6f} for the next model call; "
                    f"${max(0.0, available):.6f} remains"
                )
            self._calls[request_id] = {
                "status": "reserved",
                "model": model,
                "created_at": time.time(),
                "input_tokens_upper_bound": input_tokens_upper_bound,
                "output_tokens_upper_bound": output_tokens_upper_bound,
                "input_price_ceiling_per_million": input_price_ceiling,
                "output_price_ceiling_per_million": output_price_ceiling,
                "reserved_cost_usd": reservation,
                "estimated_cost_usd": None,
                "reported_cost_usd": None,
                "accounted_cost_usd": None,
            }
            self._write_locked()
            return reservation

    def settle(
        self,
        request_id: str,
        *,
        input_tokens: int,
        output_tokens: int,
        reported_cost_usd: float | None = None,
    ) -> dict[str, float | None]:
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("reported token usage must be non-negative")
        if reported_cost_usd is not None and (
            not math.isfinite(reported_cost_usd) or reported_cost_usd < 0
        ):
            raise ValueError("reported_cost_usd must be non-negative and finite")
        with self._lock:
            call = self._calls.get(request_id)
            if call is None or call["status"] != "reserved":
                raise RuntimeError(f"model request {request_id} has no active reservation")
            estimated = (
                input_tokens * call["input_price_ceiling_per_million"]
                + output_tokens * call["output_price_ceiling_per_million"]
            ) / 1_000_000
            estimated = _money(estimated)
            reported = _money(reported_cost_usd) if reported_cost_usd is not None else None
            accounted = max(estimated, reported or 0.0)
            reservation = float(call["reserved_cost_usd"])
            call.update(
                status="settled",
                settled_at=time.time(),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost_usd=estimated,
                reported_cost_usd=reported,
                accounted_cost_usd=_money(accounted),
            )
            if accounted > reservation + 1e-12:
                call["reservation_bound_exceeded"] = True
                self._halted_reason = (
                    f"request {request_id} cost ${accounted:.6f} exceeded its "
                    f"${reservation:.6f} pre-request reservation"
                )
            self._write_locked()
            return {
                "reserved_cost_usd": float(call["reserved_cost_usd"]),
                "estimated_cost_usd": estimated,
                "reported_cost_usd": reported,
                "accounted_cost_usd": _money(accounted),
            }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            spent = self.spent_usd
            reserved = self.reserved_usd
            return {
                "version": self.VERSION,
                "created_at": self._created_at,
                "updated_at": time.time(),
                "max_cost_usd": _money(self.max_cost_usd),
                "accounted_spent_usd": _money(spent),
                "active_reserved_usd": _money(reserved),
                "available_usd": _money(
                    0.0 if self._halted_reason else max(0.0, self.max_cost_usd - spent - reserved)
                ),
                "halted": self._halted_reason is not None,
                "halted_reason": self._halted_reason,
                "accounting_policy": (
                    "Successful calls use configured token price ceilings or a higher provider-"
                    "reported cost. Errors and incomplete usage retain their full reservation."
                ),
                "calls": dict(self._calls),
            }

    def _committed_locked(self) -> float:
        return sum(
            float(call.get("accounted_cost_usd") or 0)
            if call["status"] == "settled"
            else float(call.get("reserved_cost_usd") or 0)
            if call["status"] == "reserved"
            else 0.0
            for call in self._calls.values()
        )

    def _write_locked(self) -> None:
        payload = self.snapshot()
        temporary = self.path.with_name(
            f".{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        temporary.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        for attempt in range(5):
            try:
                os.replace(temporary, self.path)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                # Windows virus scanners/indexers can briefly hold the destination.
                time.sleep(0.01 * (attempt + 1))
