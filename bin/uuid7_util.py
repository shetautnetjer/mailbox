#!/usr/bin/env python3
from __future__ import annotations

import time
import uuid

PREFIXES = {
    "env": "envelope_id",
    "evt": "event_id",
    "del": "delivery_id",
    "rcpt": "receipt_id",
    "ack": "ack_id",
    "tsk": "task_id",
    "wi": "work_item_id",
    "trc": "trace_id",
    "hoff": "handoff_id",
    "promo": "promotion_id",
}


def gen_uuidv7(prefix: str = "") -> str:
    try:
        if hasattr(uuid, "uuid7"):
            raw = str(uuid.uuid7())
        else:
            ts_ms = int(time.time() * 1000)
            ts_hex = f"{ts_ms:012x}"
            rand_hex = uuid.uuid4().hex[12:]
            raw_hex = ts_hex + rand_hex
            raw = (
                raw_hex[:8]
                + "-"
                + raw_hex[8:12]
                + "-"
                + "7"
                + raw_hex[13:16]
                + "-"
                + hex((int(raw_hex[16:18], 16) & 0x3F) | 0x80)[2:].zfill(2)
                + raw_hex[18:20]
                + "-"
                + raw_hex[20:32]
            )
    except Exception:
        raw = str(uuid.uuid4())
    return f"{prefix}_{raw}" if prefix else raw


def gen_envelope_id() -> str:
    return gen_uuidv7("env")


def gen_event_id() -> str:
    return gen_uuidv7("evt")


def gen_delivery_id() -> str:
    return gen_uuidv7("del")


def gen_receipt_id() -> str:
    return gen_uuidv7("rcpt")


def gen_ack_id() -> str:
    return gen_uuidv7("ack")
