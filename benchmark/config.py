"""Small, explicit experiment configuration; credentials never enter manifests."""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from urllib.parse import urlparse


def names(value: str) -> list[str]:
    parsed = json.loads(value) if value.strip().startswith("[") else value.split(",")
    if not isinstance(parsed, list) or any(not isinstance(v, str) for v in parsed):
        raise ValueError("Model list must be a JSON string array or comma-separated IDs")
    return list(dict.fromkeys(v.strip() for v in parsed if v.strip()))


@dataclass
class Model:
    id: str
    base_url: str | None
    api_key: str | None = field(repr=False)
    extra_body: dict = field(default_factory=dict)
    input_price: float | None = None
    output_price: float | None = None
    temperature: float | None | str = "task_default"

    def public(self):
        return {"id": self.id, "base_url": self.base_url, "extra_body": self.extra_body,
                "input_price": self.input_price, "output_price": self.output_price,
                "temperature": self.temperature}


def configure_openrouter_budget(model: Model) -> None:
    """Fail closed and attach OpenRouter's per-million routing price ceilings."""
    if urlparse(model.base_url or "").hostname != "openrouter.ai":
        raise ValueError(
            f"USD budgeting supports only the OpenRouter endpoint; unsupported model endpoint: {model.id}"
        )
    for name, value in (("input", model.input_price), ("output", model.output_price)):
        if value is None:
            raise ValueError(
                f"MODEL_PRICES_JSON must define explicit input/output ceilings for {model.id}"
            )
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            raise ValueError(f"Invalid {name} price ceiling for {model.id}")
    provider = dict(model.extra_body.get("provider") or {})
    existing = provider.get("max_price") or {}
    if existing and not isinstance(existing, dict):
        raise ValueError("OPENROUTER_PROVIDER.max_price must be an object")
    try:
        existing_prompt = float(existing.get("prompt", model.input_price))
        existing_completion = float(existing.get("completion", model.output_price))
    except (TypeError, ValueError) as error:
        raise ValueError("OPENROUTER_PROVIDER.max_price values must be numbers") from error
    if any(
        not math.isfinite(value) or value < 0
        for value in (existing_prompt, existing_completion)
    ):
        raise ValueError("OPENROUTER_PROVIDER.max_price values must be non-negative and finite")
    provider["max_price"] = {
        "prompt": min(float(model.input_price), existing_prompt),
        "completion": min(float(model.output_price), existing_completion),
        "request": 0.0,
    }
    provider["require_parameters"] = True
    model.input_price = provider["max_price"]["prompt"]
    model.output_price = provider["max_price"]["completion"]
    model.extra_body = {**model.extra_body, "provider": provider}


def _legacy_model() -> Model:
    return Model(os.getenv("OPENAI_MODEL", "DeepSeek-V4-Flash-0731"),
                 os.getenv("OPENAI_BASE_URL"), os.getenv("OPENAI_API_KEY"),
                 input_price=float(os.environ["INPUT_PRICE_PER_MILLION"]) if os.getenv("INPUT_PRICE_PER_MILLION") else None,
                 output_price=float(os.environ["OUTPUT_PRICE_PER_MILLION"]) if os.getenv("OUTPUT_PRICE_PER_MILLION") else None)


def models_from_env(*, include_legacy: bool = False) -> list[Model]:
    """Load the OpenRouter matrix, optionally appending the existing OPENAI_* model."""
    ids = names(os.getenv("OPENROUTER_MODELS", ""))
    if ids:
        provider = json.loads(os.getenv("OPENROUTER_PROVIDER", "{}") or "{}")
        if not isinstance(provider, dict):
            raise ValueError("OPENROUTER_PROVIDER must be a JSON object")
        prices = json.loads(os.getenv("MODEL_PRICES_JSON", "{}") or "{}")
        temperatures = json.loads(os.getenv("MODEL_TEMPERATURES_JSON", "{}") or "{}")
        if not isinstance(temperatures, dict):
            raise ValueError("MODEL_TEMPERATURES_JSON must be an object")
        for model, value in temperatures.items():
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
            ):
                raise ValueError(f"Invalid temperature override for {model}")
        configured = [Model(model, os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
                            os.getenv("OPENROUTER_API_KEY"), {"provider": provider} if provider else {},
                            prices.get(model, {}).get("input"), prices.get(model, {}).get("output"),
                            temperatures[model] if model in temperatures else "task_default") for model in ids]
        if include_legacy:
            configured.append(_legacy_model())
        return configured
    return [_legacy_model()]
