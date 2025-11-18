#!/usr/bin/env python3
import argparse, time, json, random
import requests as R

PROBE_ENDPOINTS = ["/api/products", "/api/recommendations", "/api/cart"]
PROBE_CHECKOUT_ENDPOINT = "/api/checkout"
PROBE_PRODUCTS = [
    "OLJCESPC7Z", "66VCHSJNUP", "9SIQT8TOJO", "1YMWWN1N4O",
    "L9ECAV7KIM", "2ZYFJ3GM2N", "0PUK6V6EV0", "LS4PSXUNUM"
]
PROBE_CHECKOUT = {
    "email": "resilience@example.com",
    "streetAddress": "107 SW 7TH ST",
    "city": "Miami",
    "state": "FL",
    "zipCode": "33130",
    "country": "USA",
    "firstName": "Resilience",
    "lastName": "Bot",
    "creditCard": {
        "creditCardNumber": "4432-8015-6152-0454",
        "creditCardExpirationMonth": 1,
        "creditCardExpirationYear": 2030,
        "creditCardCvv": 672
    }
}


def frontend_probe(base_url, attempts=2, timeout=6, enable_checkout=True):
    base = base_url.rstrip("/")
    ok = 0
    detail = []
    endpoints = PROBE_ENDPOINTS + ([PROBE_CHECKOUT_ENDPOINT] if enable_checkout else [])
    label_map = {}
    per_endpoint = {}
    for endpoint in endpoints:
        method = "POST" if endpoint == PROBE_CHECKOUT_ENDPOINT else "GET"
        label = f"{method} {endpoint}"
        label_map[endpoint] = label
        per_endpoint[label] = {"ok": 0, "total": 0}
    if not endpoints:
        return 0, 0, detail, {}
    for _ in range(max(1, attempts)):
        endpoint = random.choice(endpoints)
        label = label_map[endpoint]
        per_endpoint[label]["total"] += 1
        url = f"{base}{endpoint}"
        try:
            method = "GET"
            if endpoint == PROBE_CHECKOUT_ENDPOINT:
                session = R.Session()
                item = random.choice(PROBE_PRODUCTS)
                add = session.post(f"{base}/api/cart", json={"item": {"productId": item, "quantity": 1}}, timeout=timeout)
                add.raise_for_status()
                payload = dict(PROBE_CHECKOUT)
                payload["email"] = f"resilience+{random.randint(1,999999)}@example.com"
                checkout_url = f"{url}?currencyCode=USD"
                resp = session.post(checkout_url, json=payload, timeout=timeout)
                method = "POST"
            else:
                resp = R.get(url, timeout=timeout)
            if 200 <= resp.status_code < 300:
                pass
            elif resp.status_code in (401, 403):
                detail.append({"endpoint": url, "method": method, "status": "skip", "code": resp.status_code})
                ok += 1
                per_endpoint[label]["ok"] += 1
                continue
            else:
                resp.raise_for_status()
            data = {}
            try:
                data = resp.json()
            except ValueError:
                data = {}
            if endpoint == PROBE_CHECKOUT_ENDPOINT:
                order_id = data.get("orderId") or data.get("order", {}).get("orderId")
                if not order_id:
                    raise RuntimeError("checkout missing orderId")
            elif not data:
                raise RuntimeError("empty response")
            ok += 1
            per_endpoint[label]["ok"] += 1
            detail.append({"endpoint": url, "method": method, "status": "ok"})
        except Exception as exc:
            detail.append({"endpoint": url, "method": method, "status": "fail", "error": str(exc)})
    return ok, max(1, attempts), detail, per_endpoint


def read_window_log(path):
    data = []
    try:
        with open(path) as fh:
            for line in fh:
                line=line.strip()
                if not line:
                    continue
                try:
                    data.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except FileNotFoundError:
        pass
    return data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, default=30)
    ap.add_argument("--probe-frontend", default="http://localhost:8080",
                    help="Frontend base URL for functional probes.")
    ap.add_argument("--window-log", default="window_log.jsonl",
                    help="Path to window log emitted by compose_chaos.sh; used for annotations.")
    ap.add_argument("--probe-attempts", type=int, default=2)
    ap.add_argument("--probe-checkout", action="store_true", default=False)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    # Align measurement with chaos window
    time.sleep(max(1, a.window))

    ok_probe, total_probe, probe_detail, per_endpoint = frontend_probe(
        a.probe_frontend,
        attempts=max(1, a.probe_attempts),
        enable_checkout=a.probe_checkout
    )
    probe_fail = total_probe - ok_probe
    probe_ratio = (ok_probe / total_probe) if total_probe else 0.0
    R_live = probe_ratio

    detail = {
        "probe_ok": ok_probe,
        "probe_total": total_probe,
        "probe_fail": probe_fail,
        "probe_detail": probe_detail,
        "measurement_mode": "probes"
    }

    killed_services = []
    last_log = {}
    if a.window_log:
        entries = read_window_log(a.window_log)
        if entries:
            last_log = entries[-1]
            killed_services = last_log.get("services") or []
    detail["killed_services"] = killed_services

    out = {"R_live": R_live, "per_endpoint": per_endpoint, "detail": detail, "window_log": last_log}
    with open(a.out, "w") as f:
        json.dump(out, f)
    print(json.dumps(out))


if __name__ == "__main__":
    main()
