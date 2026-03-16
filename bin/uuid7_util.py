#!/usr/bin/env python3
"""
uuid7_util.py — Shared UUIDv7 generator for the agent system.

UUIDv7 Doctrine:
  - Operational IDs use UUIDv7 with type prefixes
  - Semantic/knowledge IDs stay human-readable
  - Prefixes: env_, evt_, art_, epi_, gate_, rcpt_, tsk_, wi_, ses_, trc_

Usage:
  from uuid7_util import gen_uuidv7

  envelope_id = gen_uuidv7("env")   # env_019cd718-3329-7xxx-xxxx-xxxxxxxxxxxx
  event_id = gen_uuidv7("evt")      # evt_019cd718-...
  artifact_id = gen_uuidv7("art")   # art_019cd718-...
  raw_uuid = gen_uuidv7()            # 019cd718-3329-7xxx-xxxx-xxxxxxxxxxxx
"""
from __future__ import annotations

import time
import uuid


# Standard type prefixes per UUIDv7 rollout checklist
PREFIXES = {
    "env": "envelope_id",
    "evt": "event_id",
    "art": "artifact_id",
    "epi": "episode_id",
    "gate": "gate_id",
    "rcpt": "receipt_id",
    "tsk": "task_id",
    "wi": "work_item_id",
    "ses": "session_id",
    "trc": "trace_id",
    "promo": "promotion_id",
    "hoff": "handoff_id",
    "obs": "observation_id",
}


def gen_uuidv7(prefix: str = "") -> str:
    """Generate a UUIDv7 (time-ordered UUID) with optional type prefix.

    UUIDv7 provides:
      - Monotonically increasing (time-ordered)
      - Globally unique
      - Sortable by creation time
      - 48-bit ms timestamp + 74 random bits

    Format: {prefix}_{uuidv7} or just {uuidv7} if no prefix.
    """
    try:
        # Python 3.12+ has uuid.uuid7 — use native implementation
        if hasattr(uuid, 'uuid7'):
            raw = str(uuid.uuid7())
        else:
            # Manual UUIDv7 construction (RFC 9562)
            ts_ms = int(time.time() * 1000)
            ts_hex = f"{ts_ms:012x}"
            rand_hex = uuid.uuid4().hex[12:]
            raw_hex = ts_hex + rand_hex
            # Set version 7 (bits 48-51) and variant 10 (bits 64-65)
            raw = (
                raw_hex[:8] + "-" +
                raw_hex[8:12] + "-" +
                "7" + raw_hex[13:16] + "-" +
                hex((int(raw_hex[16:18], 16) & 0x3F) | 0x80)[2:].zfill(2) +
                raw_hex[18:20] + "-" +
                raw_hex[20:32]
            )
    except Exception:
        raw = str(uuid.uuid4())

    return f"{prefix}_{raw}" if prefix else raw


# Convenience functions for each operational ID type
def gen_envelope_id() -> str:
    return gen_uuidv7("env")

def gen_event_id() -> str:
    return gen_uuidv7("evt")

def gen_artifact_id() -> str:
    return gen_uuidv7("art")

def gen_episode_id() -> str:
    return gen_uuidv7("epi")

def gen_gate_id() -> str:
    return gen_uuidv7("gate")

def gen_receipt_id() -> str:
    return gen_uuidv7("rcpt")

def gen_task_id() -> str:
    return gen_uuidv7("tsk")

def gen_work_item_id() -> str:
    return gen_uuidv7("wi")

def gen_session_id() -> str:
    return gen_uuidv7("ses")

def gen_trace_id() -> str:
    return gen_uuidv7("trc")

def gen_observation_id() -> str:
    return gen_uuidv7("obs")


if __name__ == "__main__":
    # Quick test
    print("UUIDv7 Generator Test:")
    for name, desc in PREFIXES.items():
        uid = gen_uuidv7(name)
        print(f"  {desc:20s} → {uid}")
    print(f"  {'raw':20s} → {gen_uuidv7()}")
