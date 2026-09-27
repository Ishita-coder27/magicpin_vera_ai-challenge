import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "bot.py").exists())
sys.path.insert(0, str(ROOT))
EXP = ROOT / "dataset" / "expanded"


def _ensure_expanded():
    if not (EXP / "test_pairs.json").exists():
        subprocess.run([sys.executable, "generate_dataset.py", "--out", "./expanded"], cwd=ROOT / "dataset", check=True)


@pytest.fixture(scope="session")
def data():
    _ensure_expanded()

    def load(sub, key):
        return {json.loads(p.read_text())[key]: json.loads(p.read_text()) for p in (EXP / sub).glob("*.json")}

    cats = {json.loads(p.read_text())["slug"]: json.loads(p.read_text()) for p in (EXP / "categories").glob("*.json")}
    return {
        "categories": cats,
        "merchants": load("merchants", "merchant_id"),
        "customers": load("customers", "customer_id"),
        "triggers": load("triggers", "id"),
        "pairs": json.loads((EXP / "test_pairs.json").read_text())["pairs"],
    }


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    import bot
    bot.ENGINE.teardown()
    with TestClient(bot.app) as c:
        yield c
    bot.ENGINE.teardown()


def push(client, scope, cid, payload, version=1):
    return client.post("/v1/context", json={"scope": scope, "context_id": cid, "version": version,
                                             "payload": payload, "delivered_at": "2026-04-26T10:00:00Z"})


@pytest.fixture()
def loaded(client, data):
    for slug, c in data["categories"].items():
        push(client, "category", slug, c)
    for mid, m in data["merchants"].items():
        push(client, "merchant", mid, m)
    for cid, c in data["customers"].items():
        push(client, "customer", cid, c)
    return client
