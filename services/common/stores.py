"""Persistence. Two implementations with one interface:

* DynamoStores: the deployed path (and the test path, under moto).
* LocalStores:  JSON files under demo/state for offline simulation; MemoryStores
                is the same class with no path.

Money-moving writes go through `commit_money_move`, which is atomic:
nonce consumed + ledger entry appended + order updated, or nothing.
Only services/executor may call it (tests/test_boundary.py enforces that).
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from botocore.exceptions import ClientError

from . import clock
from .config import settings
from .tables import TABLE_SPECS


class ReplayError(Exception):
    """The nonce was already consumed."""


class ConflictError(Exception):
    """A conditional write failed (state changed under us)."""


# ----------------------------------------------------------------------------- helpers
def to_ddb(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: to_ddb(v) for k, v in value.items() if v is not None}
    if isinstance(value, (list, tuple)):
        return [to_ddb(v) for v in value]
    return value


def from_ddb(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: from_ddb(v) for k, v in value.items()}
    if isinstance(value, list):
        return [from_ddb(v) for v in value]
    return value


_counter = 0


def ts_key() -> str:
    """Monotonic, sortable timestamp key: 12-digit epoch seconds + 6-digit per-process counter.
    Keeps order deterministic even when many writes share one second (or a frozen test clock)."""
    global _counter
    _counter = (_counter + 1) % 1_000_000
    return f"{clock.now():012d}{_counter:06d}"


def ts_since(ts: int) -> str:
    return f"{int(ts):012d}000000"


def ts_epoch(key: str) -> int:
    return int(str(key)[:12])


def day_of(ts: int) -> str:
    import time

    return time.strftime("%Y-%m-%d", time.gmtime(ts))


def _build_update(updates: dict[str, Any]) -> tuple[str, dict, dict]:
    names, values, sets = {}, {}, []
    for i, (k, v) in enumerate(updates.items()):
        parts = k.split(".")
        path = ".".join(f"#n{i}_{j}" for j in range(len(parts)))
        for j, p in enumerate(parts):
            names[f"#n{i}_{j}"] = p
        values[f":v{i}"] = to_ddb(v)
        sets.append(f"{path} = :v{i}")
    return "SET " + ", ".join(sets), names, values


def _set_path(doc: dict, dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    cur = doc
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = value


# ----------------------------------------------------------------------------- DynamoDB
class DynamoStores:
    def __init__(self, resource=None, names: dict[str, str] | None = None):
        from .aws import resource as _resource

        self._res = resource or _resource("dynamodb")
        self._names = names or {t: settings.table(t) for t in TABLE_SPECS}
        self._client = self._res.meta.client

    def _t(self, logical: str):
        return self._res.Table(self._names[logical])

    # orders -----------------------------------------------------------------
    def get_order(self, order_id: str) -> dict | None:
        item = self._t("orders").get_item(Key={"order_id": order_id}, ConsistentRead=True).get("Item")
        return from_ddb(item) if item else None

    def put_order(self, order: dict) -> None:
        self._t("orders").put_item(Item=to_ddb(order), ConditionExpression="attribute_not_exists(order_id)")

    def update_order(self, order_id: str, updates: dict, expected_status: str | Iterable[str] | None = None) -> dict:
        updates = {**updates, "updated_at": clock.now()}
        expr, names, values = _build_update(updates)
        kwargs: dict[str, Any] = {
            "Key": {"order_id": order_id},
            "UpdateExpression": expr,
            "ExpressionAttributeNames": names,
            "ExpressionAttributeValues": values,
            "ReturnValues": "ALL_NEW",
        }
        if expected_status is not None:
            statuses = [expected_status] if isinstance(expected_status, str) else list(expected_status)
            names["#st"] = "status"
            placeholders = []
            for i, s in enumerate(statuses):
                values[f":st{i}"] = s
                placeholders.append(f":st{i}")
            kwargs["ConditionExpression"] = f"#st IN ({', '.join(placeholders)})"
        try:
            return from_ddb(self._t("orders").update_item(**kwargs)["Attributes"])
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ConflictError(f"order {order_id} not in expected status {expected_status}") from e
            raise

    def order_by_checkout(self, checkout_id: str) -> dict | None:
        r = self._t("orders").query(
            IndexName="by_checkout",
            KeyConditionExpression="checkout_id = :c",
            ExpressionAttributeValues={":c": checkout_id},
        )
        items = r.get("Items") or []
        return from_ddb(items[0]) if items else None

    def orders_for_buyer(self, buyer_sub: str) -> list[dict]:
        r = self._t("orders").query(
            IndexName="by_buyer",
            KeyConditionExpression="buyer_sub = :b",
            ExpressionAttributeValues={":b": buyer_sub},
            ScanIndexForward=False,
        )
        return [from_ddb(i) for i in r.get("Items", [])]

    def orders_for_seller(self, seller_sub: str) -> list[dict]:
        r = self._t("orders").query(
            IndexName="by_seller",
            KeyConditionExpression="seller_sub = :s",
            ExpressionAttributeValues={":s": seller_sub},
            ScanIndexForward=False,
        )
        return [from_ddb(i) for i in r.get("Items", [])]

    def find_order_by_token(self, token: str) -> tuple[dict, str] | None:
        """Locate the order parked on a task token. Tiny tables; a filtered scan is fine."""
        for phase in ("payment", "dispatch", "delivery", "dispute", "approval"):
            r = self._t("orders").scan(FilterExpression="tokens.#p = :t", ExpressionAttributeNames={"#p": phase},
                                       ExpressionAttributeValues={":t": token})
            items = r.get("Items") or []
            if items:
                return from_ddb(items[0]), phase
        return None

    # ledger -----------------------------------------------------------------
    def ledger_entries(self, order_id: str) -> list[dict]:
        r = self._t("ledger").query(
            KeyConditionExpression="order_id = :o", ExpressionAttributeValues={":o": order_id}, ConsistentRead=True
        )
        return sorted((from_ddb(i) for i in r.get("Items", [])), key=lambda e: (int(e.get("seq", 0)), e.get("ts", "")))

    def count_refunds_for_buyer(self, buyer_sub: str, since_ts: int) -> int:
        r = self._t("ledger").query(
            IndexName="by_buyer",
            KeyConditionExpression="buyer_sub = :b AND ts >= :s",
            FilterExpression="#t = :rf",
            ExpressionAttributeNames={"#t": "type"},
            ExpressionAttributeValues={":b": buyer_sub, ":s": ts_since(since_ts), ":rf": "REFUND"},
        )
        return len(r.get("Items", []))

    def commit_money_move(self, nonce: str, nonce_expires_at: int, entry: dict, order_id: str,
                          order_updates: dict, expected_status: Iterable[str], expected_version: int = 0) -> None:
        """Atomic: consume nonce, append ledger entry, update order (status + version checked).
        Raises ReplayError / ConflictError; on any failure nothing is written."""
        entry = dict(entry)
        entry.setdefault("ts", ts_key())
        entry["seq"] = int(expected_version) + 1
        order_updates = {**order_updates, "updated_at": clock.now(), "version": int(expected_version) + 1}
        expr, names, values = _build_update(order_updates)
        statuses = list(expected_status)
        names["#st"] = "status"
        names["#ver"] = "version"
        values[":expver"] = int(expected_version)
        for i, s in enumerate(statuses):
            values[f":st{i}"] = s
        cond = (f"#st IN ({', '.join(f':st{i}' for i in range(len(statuses)))}) AND "
                f"(#ver = :expver OR (attribute_not_exists(#ver) AND :expver = :zero))")
        values[":zero"] = 0
        try:
            self._client.transact_write_items(
                TransactItems=[
                    {
                        "Put": {
                            "TableName": self._names["nonces"],
                            "Item": to_ddb({"nonce": nonce, "expires_at": nonce_expires_at, "order_id": order_id,
                                            "entry_id": entry["entry_id"], "consumed_at": clock.now()}),
                            "ConditionExpression": "attribute_not_exists(nonce)",
                        }
                    },
                    {
                        "Put": {
                            "TableName": self._names["ledger"],
                            "Item": to_ddb(entry),
                            "ConditionExpression": "attribute_not_exists(entry_id)",
                        }
                    },
                    {
                        "Update": {
                            "TableName": self._names["orders"],
                            "Key": {"order_id": order_id},
                            "UpdateExpression": expr,
                            "ConditionExpression": cond,
                            "ExpressionAttributeNames": names,
                            "ExpressionAttributeValues": values,
                        }
                    },
                ]
            )
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code == "TransactionCanceledException":
                reasons = e.response.get("CancellationReasons") or []
                if reasons and reasons[0].get("Code") == "ConditionalCheckFailed":
                    raise ReplayError(f"nonce {nonce} already consumed") from e
                raise ConflictError(str(reasons or e)) from e
            raise

    # nonces (read only; consumption happens inside commit_money_move) -------
    def nonce_consumed(self, nonce: str) -> bool:
        return "Item" in self._t("nonces").get_item(Key={"nonce": nonce}, ConsistentRead=True)

    # decisions --------------------------------------------------------------
    def put_decision(self, record: dict) -> None:
        record = dict(record)
        record["ts"] = ts_key()
        record.setdefault("day", day_of(ts_epoch(record["ts"])))
        self._t("decisions").put_item(Item=to_ddb(record))

    def decisions_for_order(self, order_id: str) -> list[dict]:
        r = self._t("decisions").query(
            IndexName="by_order", KeyConditionExpression="order_id = :o", ExpressionAttributeValues={":o": order_id}
        )
        return [from_ddb(i) for i in r.get("Items", [])]

    def decisions_since(self, since_ts: int, limit: int = 50) -> list[dict]:
        out: list[dict] = []
        for day in sorted({day_of(since_ts), day_of(clock.now())}):
            r = self._t("decisions").query(
                IndexName="by_day",
                KeyConditionExpression="#d = :d AND ts >= :s",
                ExpressionAttributeNames={"#d": "day"},
                ExpressionAttributeValues={":d": day, ":s": ts_since(since_ts)},
                Limit=limit,
            )
            out.extend(from_ddb(i) for i in r.get("Items", []))
        return sorted(out, key=lambda d: d["ts"])[-limit:]

    # challenges -------------------------------------------------------------
    def put_challenge(self, challenge: dict) -> None:
        self._t("challenges").put_item(Item=to_ddb(challenge))

    def get_challenge(self, order_id: str, challenge_id: str) -> dict | None:
        item = self._t("challenges").get_item(Key={"order_id": order_id, "challenge_id": challenge_id},
                                              ConsistentRead=True).get("Item")
        return from_ddb(item) if item else None

    def update_challenge(self, order_id: str, challenge_id: str, updates: dict) -> dict:
        expr, names, values = _build_update(updates)
        r = self._t("challenges").update_item(
            Key={"order_id": order_id, "challenge_id": challenge_id},
            UpdateExpression=expr, ExpressionAttributeNames=names, ExpressionAttributeValues=values,
            ReturnValues="ALL_NEW",
        )
        return from_ddb(r["Attributes"])

    def challenges_for_order(self, order_id: str) -> list[dict]:
        r = self._t("challenges").query(
            KeyConditionExpression="order_id = :o", ExpressionAttributeValues={":o": order_id}, ConsistentRead=True
        )
        return sorted((from_ddb(i) for i in r.get("Items", [])), key=lambda c: c.get("created_at", 0))

    # items ------------------------------------------------------------------
    def get_item(self, item_id: str) -> dict | None:
        item = self._t("items").get_item(Key={"item_id": item_id}).get("Item")
        return from_ddb(item) if item else None

    def put_item(self, item: dict) -> None:
        self._t("items").put_item(Item=to_ddb(item))

    def list_items(self) -> list[dict]:
        return [from_ddb(i) for i in self._t("items").scan().get("Items", [])]

    def reserve_stock(self, item_id: str, qty: int = 1) -> bool:
        try:
            self._t("items").update_item(
                Key={"item_id": item_id},
                UpdateExpression="SET stock = stock - :q",
                ConditionExpression="stock >= :q",
                ExpressionAttributeValues={":q": qty},
            )
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    # parties ----------------------------------------------------------------
    def put_party(self, party: dict) -> None:
        self._t("parties").put_item(Item=to_ddb(party))

    def get_party(self, party_id: str) -> dict | None:
        item = self._t("parties").get_item(Key={"party_id": party_id}).get("Item")
        return from_ddb(item) if item else None

    def party_by_phone(self, phone: str) -> dict | None:
        r = self._t("parties").query(IndexName="by_phone", KeyConditionExpression="phone = :p",
                                     ExpressionAttributeValues={":p": phone})
        items = r.get("Items") or []
        return from_ddb(items[0]) if items else None

    def party_by_sub(self, sub: str) -> dict | None:
        r = self._t("parties").query(IndexName="by_sub", KeyConditionExpression="#s = :s",
                                     ExpressionAttributeNames={"#s": "sub"}, ExpressionAttributeValues={":s": sub})
        items = r.get("Items") or []
        return from_ddb(items[0]) if items else None

    def list_parties(self) -> list[dict]:
        return [from_ddb(i) for i in self._t("parties").scan().get("Items", [])]


# ----------------------------------------------------------------------------- Local / memory
class LocalStores:
    """Same interface as DynamoStores, backed by dicts (optionally persisted to JSON)."""

    def __init__(self, path: str | Path | None = None):
        self._path = Path(path) if path else None
        self._lock = threading.RLock()
        self._db: dict[str, dict] = {t: {} for t in TABLE_SPECS}
        if self._path and self._path.exists():
            self._db.update(json.loads(self._path.read_text()))
            for t in TABLE_SPECS:
                self._db.setdefault(t, {})

    def _flush(self) -> None:
        if self._path:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._db, indent=1, default=str))

    @staticmethod
    def _k(*parts: str) -> str:
        return "|".join(parts)

    # orders
    def get_order(self, order_id):
        o = self._db["orders"].get(order_id)
        return json.loads(json.dumps(o)) if o else None

    def put_order(self, order):
        with self._lock:
            if order["order_id"] in self._db["orders"]:
                raise ConflictError("order exists")
            self._db["orders"][order["order_id"]] = json.loads(json.dumps(order))
            self._flush()

    def update_order(self, order_id, updates, expected_status=None):
        with self._lock:
            o = self._db["orders"].get(order_id)
            if o is None:
                raise ConflictError("order missing")
            if expected_status is not None:
                allowed = [expected_status] if isinstance(expected_status, str) else list(expected_status)
                if o.get("status") not in allowed:
                    raise ConflictError(f"order {order_id} not in expected status {allowed}")
            for k, v in {**updates, "updated_at": clock.now()}.items():
                _set_path(o, k, v)
            self._flush()
            return json.loads(json.dumps(o))

    def order_by_checkout(self, checkout_id):
        for o in self._db["orders"].values():
            if o.get("checkout_id") == checkout_id:
                return json.loads(json.dumps(o))
        return None

    def orders_for_buyer(self, buyer_sub):
        rows = [o for o in self._db["orders"].values() if o.get("buyer_sub") == buyer_sub]
        return sorted((json.loads(json.dumps(o)) for o in rows), key=lambda o: o.get("created_at", 0), reverse=True)

    def orders_for_seller(self, seller_sub):
        rows = [o for o in self._db["orders"].values() if o.get("seller_sub") == seller_sub]
        return sorted((json.loads(json.dumps(o)) for o in rows), key=lambda o: o.get("created_at", 0), reverse=True)

    def find_order_by_token(self, token):
        for o in self._db["orders"].values():
            for phase, t in (o.get("tokens") or {}).items():
                if t == token:
                    return json.loads(json.dumps(o)), phase
        return None

    # ledger
    def ledger_entries(self, order_id):
        rows = [e for e in self._db["ledger"].values() if e["order_id"] == order_id]
        return sorted(rows, key=lambda e: (int(e.get("seq", 0)), e.get("ts", "")))

    def count_refunds_for_buyer(self, buyer_sub, since_ts):
        return sum(1 for e in self._db["ledger"].values()
                   if e.get("buyer_sub") == buyer_sub and e.get("type") == "REFUND"
                   and ts_epoch(e.get("ts", "0")) >= since_ts)

    def commit_money_move(self, nonce, nonce_expires_at, entry, order_id, order_updates, expected_status, expected_version=0):
        with self._lock:
            if nonce in self._db["nonces"]:
                raise ReplayError(f"nonce {nonce} already consumed")
            o = self._db["orders"].get(order_id)
            if o is None or o.get("status") not in list(expected_status):
                raise ConflictError(f"order {order_id} not in expected status {list(expected_status)}")
            if int(o.get("version", 0)) != int(expected_version):
                raise ConflictError(f"order {order_id} changed underneath (version {o.get('version')} != {expected_version})")
            entry = dict(entry)
            entry.setdefault("ts", ts_key())
            entry["seq"] = int(expected_version) + 1
            order_updates = {**order_updates, "version": int(expected_version) + 1}
            if self._k(order_id, entry["entry_id"]) in self._db["ledger"]:
                raise ConflictError("ledger entry exists")
            self._db["nonces"][nonce] = {"nonce": nonce, "expires_at": nonce_expires_at, "order_id": order_id,
                                         "entry_id": entry["entry_id"], "consumed_at": clock.now()}
            self._db["ledger"][self._k(order_id, entry["entry_id"])] = entry
            for k, v in {**order_updates, "updated_at": clock.now()}.items():
                _set_path(o, k, v)
            self._flush()

    def nonce_consumed(self, nonce):
        return nonce in self._db["nonces"]

    # decisions
    def put_decision(self, record):
        record = dict(record)
        record["ts"] = ts_key()
        record.setdefault("day", day_of(ts_epoch(record["ts"])))
        with self._lock:
            self._db["decisions"][record["decision_id"]] = record
            self._flush()

    def decisions_for_order(self, order_id):
        return sorted((d for d in self._db["decisions"].values() if d.get("order_id") == order_id), key=lambda d: d["ts"])

    def decisions_since(self, since_ts, limit=50):
        rows = [d for d in self._db["decisions"].values() if ts_epoch(d["ts"]) >= since_ts]
        return sorted(rows, key=lambda d: d["ts"])[-limit:]

    # challenges
    def put_challenge(self, challenge):
        with self._lock:
            self._db["challenges"][self._k(challenge["order_id"], challenge["challenge_id"])] = dict(challenge)
            self._flush()

    def get_challenge(self, order_id, challenge_id):
        c = self._db["challenges"].get(self._k(order_id, challenge_id))
        return dict(c) if c else None

    def update_challenge(self, order_id, challenge_id, updates):
        with self._lock:
            c = self._db["challenges"][self._k(order_id, challenge_id)]
            for k, v in updates.items():
                _set_path(c, k, v)
            self._flush()
            return dict(c)

    def challenges_for_order(self, order_id):
        rows = [c for c in self._db["challenges"].values() if c["order_id"] == order_id]
        return sorted(rows, key=lambda c: c.get("created_at", 0))

    # items
    def get_item(self, item_id):
        i = self._db["items"].get(item_id)
        return dict(i) if i else None

    def put_item(self, item):
        with self._lock:
            self._db["items"][item["item_id"]] = dict(item)
            self._flush()

    def list_items(self):
        return [dict(i) for i in self._db["items"].values()]

    def reserve_stock(self, item_id, qty=1):
        with self._lock:
            i = self._db["items"].get(item_id)
            if not i or i.get("stock", 0) < qty:
                return False
            i["stock"] -= qty
            self._flush()
            return True

    # parties
    def put_party(self, party):
        with self._lock:
            self._db["parties"][party["party_id"]] = dict(party)
            self._flush()

    def get_party(self, party_id):
        p = self._db["parties"].get(party_id)
        return dict(p) if p else None

    def party_by_phone(self, phone):
        return next((dict(p) for p in self._db["parties"].values() if p.get("phone") == phone), None)

    def party_by_sub(self, sub):
        return next((dict(p) for p in self._db["parties"].values() if p.get("sub") == sub), None)

    def list_parties(self):
        return [dict(p) for p in self._db["parties"].values()]


MemoryStores = LocalStores

# ----------------------------------------------------------------------------- factory
_default: Any = None


def get_stores():
    """Process-wide store singleton selected by STORE_MODE (dynamodb | local | memory)."""
    global _default
    if _default is None:
        mode = settings.store_mode
        if mode == "dynamodb":
            _default = DynamoStores()
        elif mode == "memory":
            _default = LocalStores(None)
        else:
            from .config import repo_root

            _default = LocalStores(repo_root() / "demo" / "state" / "db.json")
    return _default


def set_stores(stores) -> None:
    global _default
    _default = stores


def create_tables(dynamodb_client, names: dict[str, str] | None = None) -> None:
    """Create the tables (moto / local DynamoDB). CDK does the same from TABLE_SPECS."""
    from .tables import key_attribute_definitions, key_schema

    names = names or {t: settings.table(t) for t in TABLE_SPECS}
    existing = set(dynamodb_client.list_tables().get("TableNames", []))
    for logical, spec in TABLE_SPECS.items():
        name = names[logical]
        if name in existing:
            continue
        kwargs: dict[str, Any] = {
            "TableName": name,
            "AttributeDefinitions": key_attribute_definitions(spec),
            "KeySchema": key_schema(spec["pk"], spec.get("sk")),
            "BillingMode": "PAY_PER_REQUEST",
        }
        if spec.get("gsis"):
            kwargs["GlobalSecondaryIndexes"] = [
                {"IndexName": n, "KeySchema": key_schema(g["pk"], g.get("sk")), "Projection": {"ProjectionType": "ALL"}}
                for n, g in spec["gsis"].items()
            ]
        dynamodb_client.create_table(**kwargs)
        if spec.get("ttl"):
            dynamodb_client.update_time_to_live(
                TableName=name, TimeToLiveSpecification={"Enabled": True, "AttributeName": spec["ttl"]}
            )
