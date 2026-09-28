from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json
import math

CATALOG = json.loads((Path(__file__).parent / 'data/metrics.json').read_text(encoding='utf-8'))


def iso(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts is not None else datetime.now(timezone.utc).isoformat()


def finite(value):
    if value is None:
        raise ValueError('Null is not an observation')
    if isinstance(value, dict):
        return {str(k): finite(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [finite(v) for v in value]
    if hasattr(value, 'item'):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError('Non-finite result; denominator, variation or sample is insufficient')
    return value


@dataclass
class Metric:
    id: int
    value: Any = None
    status: str = 'unavailable'
    unit: str = ''
    source: str = ''
    as_of: str | None = None
    frequency: str = ''
    sample: int | None = None
    note: str = ''

    def dict(self):
        return asdict(self)


class Results:
    def __init__(self, source='', as_of=None, sample=None, stale=False):
        self.source, self.as_of, self.sample, self.stale = source, as_of, sample, stale
        self.rows = {m['id']: Metric(m['id'], note=f"Requires {m['dependency']}; no eligible observation available.") for m in CATALOG}

    def put(self, i, value, unit='', note='', *, source=None, as_of=None, frequency='1d', sample=None, status=None):
        try:
            value = finite(value)
            if value is None:
                raise ValueError('Undefined result')
            self.rows[i] = Metric(i, value, status or ('stale' if self.stale else 'ok'), unit,
                                  source or self.source, as_of or self.as_of, frequency,
                                  self.sample if sample is None else sample, note)
        except (ValueError, TypeError) as exc:
            self.missing(i, str(exc))

    def calc(self, i, fn, *args, **kwargs):
        try:
            self.put(i, fn(), *args, **kwargs)
        except (ValueError, ZeroDivisionError, FloatingPointError, IndexError) as exc:
            self.missing(i, str(exc))

    def missing(self, i, reason):
        self.rows[i] = Metric(i, note=reason)

    def list(self):
        return [r.dict() for r in self.rows.values()]
