# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Verify Semantica package configuration and Neo4j connectivity without writes."""
from __future__ import annotations

import os
import sys


def main() -> int:
    required = ("SEMANTICA_URI", "SEMANTICA_USERNAME", "SEMANTICA_PASSWORD")
    missing = [name for name in required if not os.environ.get(name, "").strip()]
    if missing:
        print(f"missing environment variables: {', '.join(missing)}", file=sys.stderr)
        return 2

    try:
        import importlib.metadata as metadata

        version = metadata.version("semantica")
        from semantica.graph_store import Neo4jStore
    except Exception as exc:
        print(f"Semantica import failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3

    store = Neo4jStore(
        uri=os.environ["SEMANTICA_URI"].strip(),
        user=os.environ["SEMANTICA_USERNAME"].strip(),
        password=os.environ["SEMANTICA_PASSWORD"].strip(),
        database=os.environ.get("SEMANTICA_DATABASE", "neo4j").strip() or "neo4j",
    )
    try:
        store.connect()
    except Exception as exc:
        print(f"Neo4j connectivity failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 4
    finally:
        store.close()

    print(f"Semantica {version}: import ok")
    print("Neo4j connectivity: ok")
    print("Verification performs no graph writes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
