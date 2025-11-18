#!/usr/bin/env python3
"""Minimal tests for endpoint target evaluation logic."""
import importlib.util
import json
import sys
import tempfile
from pathlib import Path


def load_resilience_module():
    path = Path(__file__).with_name("resilience.py")
    spec = importlib.util.spec_from_file_location("resilience_test_module", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def build_sample_graph(res_module):
    graph = {
        "services": ["frontend", "checkout", "kafka", "accounting", "fraud-detection", "redis-cart"],
        "edges": [
            [0, 1],  # frontend -> checkout
            [1, 2],  # checkout -> kafka
            [2, 3],  # kafka -> accounting
            [2, 4],  # kafka -> fraud
            [1, 5],  # checkout -> redis-cart
        ],
        "entrypoints": [0],
        "async_edges": [[1, 2], [2, 3], [2, 4]],
    }
    res_module.prepare_graph(graph)
    return graph


def load_specs(res_module):
    payload = {
        "ANY": {"entry": "frontend", "any_of": ["checkout"]},
        "ALL": {"entry": "frontend", "all_of": ["checkout", "redis-cart"]},
        "KOFN": {
            "entry": "frontend",
            "k_of_n": {"k": 2, "items": ["accounting", "fraud-detection", "redis-cart"]},
        },
        "ASYNC": {
            "entry": "frontend",
            "all_of": ["checkout", "accounting", "fraud-detection"],
            "exclude_async": True,
        },
        "BLOCKING": {
            "entry": "frontend",
            "all_of": ["checkout", "accounting", "fraud-detection"],
            "exclude_async": False,
        },
    }
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as fh:
        json.dump(payload, fh)
        tmp_path = fh.name
    specs = res_module.load_targets(tmp_path)
    return specs


def main():
    res_module = load_resilience_module()
    graph = build_sample_graph(res_module)
    specs = load_specs(res_module)

    spec_any = res_module.get_endpoint_spec(specs, "ANY")
    assert res_module.endpoint_success(graph, set(), spec_any, "all-block")
    assert not res_module.endpoint_success(graph, {"checkout"}, spec_any, "all-block")

    spec_all = res_module.get_endpoint_spec(specs, "ALL")
    assert res_module.endpoint_success(graph, set(), spec_all, "all-block")
    assert not res_module.endpoint_success(graph, {"redis-cart"}, spec_all, "all-block")

    spec_k = res_module.get_endpoint_spec(specs, "KOFN")
    assert res_module.endpoint_success(graph, set(), spec_k, "all-block")
    assert not res_module.endpoint_success(
        graph, {"accounting", "fraud-detection"}, spec_k, "all-block"
    )

    spec_async = res_module.get_endpoint_spec(specs, "ASYNC")
    assert res_module.endpoint_success(
        graph, {"accounting", "fraud-detection"}, spec_async, "async"
    )

    spec_block = res_module.get_endpoint_spec(specs, "BLOCKING")
    assert not res_module.endpoint_success(
        graph, {"accounting", "fraud-detection"}, spec_block, "async"
    )

    print("tests_targets: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
