"""Manual E2E test driver for the JARVIS API under `--profile test`.

Drives the 16-scenario manual test list (2026-07-16 round 2 baseline) against a
running server and records every response verbatim:

    # terminal 1 — isolated server (JARVIS_TEST_HOME optional but recommended,
    # required for the killswitch steps D13a/D13c):
    $env:JARVIS_TEST_HOME = "C:\\path\\to\\stable-test-home"
    python -m jarvis --api --profile test --port 8132

    # terminal 2 — all tests, or a subset:
    python scripts/manual_test_driver.py A1 A2 A3
    python scripts/manual_test_driver.py --all

Environment:
    JARVIS_TEST_BASE_URL  target server (default http://127.0.0.1:8132)
    JARVIS_TEST_HOME      same value the server was started with — used only to
                          write data/kill_switch.json for D13a/D13c
    JARVIS_TEST_RESULTS   output JSONL path (default ./results.jsonl)

Every test appends one JSON object to the results file (message, verbatim
response, elapsed seconds, /status snapshot) so runs are comparable across
sprints — this file is the measurement instrument behind Faz 4's acceptance
metrics, not a pytest suite.
"""
import io
import json
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = os.environ.get("JARVIS_TEST_BASE_URL", "http://127.0.0.1:8132").rstrip("/")
RESULTS = Path(os.environ.get("JARVIS_TEST_RESULTS", "results.jsonl"))
TEST_HOME = os.environ.get("JARVIS_TEST_HOME", "")


def post_json(path: str, body: dict, timeout: int = 240) -> dict:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        BASE + path, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def get_json(path: str, timeout: int = 30) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def sse_post(path: str, body: dict, timeout: int = 240) -> str:
    """POST and read an SSE stream; join data: tokens, unescape newlines."""
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        BASE + path, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    tokens: list[str] = []
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for raw in r:
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line.startswith("data: "):
                continue
            payload = line[len("data: "):]
            if payload == "[DONE]":
                break
            tokens.append(payload.replace("\\n", "\n"))
    return "".join(tokens)


def status_snapshot() -> dict:
    try:
        s = get_json("/status")
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}
    keys = [
        "requested_role", "actual_provider", "actual_model", "fallback_used",
        "turn_had_any_fallback", "session_cost_usd", "session_unpriced_tokens",
        "cloud_policy", "cloud_calls_allowed", "vertex_configured", "degraded",
        "model",
    ]
    return {k: s.get(k) for k in keys if k in s}


def record(entry: dict) -> None:
    with RESULTS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def run_chat(test_id: str, message: str, decision: str | None = None) -> None:
    """decision: None | 'approve' | 'deny' — what to answer IF a confirmation comes back."""
    print(f"\n{'='*72}\n[{test_id}] > {message}")
    t0 = time.time()
    entry: dict = {"test_id": test_id, "message": message}
    try:
        resp = post_json("/chat", {"message": message, "language": "tr"})
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:1000]
        entry.update(error=f"HTTP {e.code}", body=body, elapsed_s=round(time.time() - t0, 1))
        print(f"  !! HTTP {e.code}: {body}")
        entry["status"] = status_snapshot()
        record(entry)
        return
    except Exception as e:  # noqa: BLE001
        entry.update(error=repr(e), elapsed_s=round(time.time() - t0, 1))
        print(f"  !! {e!r}")
        record(entry)
        return
    elapsed = round(time.time() - t0, 1)
    entry["elapsed_s"] = elapsed

    if resp.get("confirmation_required"):
        entry["confirmation"] = {"id": resp.get("id"), "payload": resp.get("payload")}
        print(f"  [CONFIRMATION REQUIRED] payload={json.dumps(resp.get('payload'), ensure_ascii=False)}")
        if decision:
            print(f"  -> decision: {decision}")
            t1 = time.time()
            try:
                cont = sse_post(f"/chat/confirm/{resp['id']}", {"decision": decision})
            except Exception as e:  # noqa: BLE001
                cont = f"[confirm stream error] {e!r}"
            entry["decision"] = decision
            entry["continuation"] = cont
            entry["confirm_elapsed_s"] = round(time.time() - t1, 1)
            print(f"  [JARVIS devam | {entry['confirm_elapsed_s']}s] {cont}")
        else:
            print("  (no decision scripted — leaving pending)")
    elif resp.get("async"):
        entry["async_task"] = resp
        print(f"  [ASYNC diverted] {resp}")
    else:
        entry["response"] = resp.get("response")
        entry["model_label"] = resp.get("model")
        print(f"  [JARVIS | {elapsed}s | {resp.get('model')}]\n  {resp.get('response')}")

    entry["status"] = status_snapshot()
    print(f"  [status] {json.dumps(entry['status'], ensure_ascii=False)}")
    record(entry)


def reset_session(next_id: str) -> None:
    """Archive the current conversation and start a fresh session (POST /reset).

    Faz 1.5 — every scenario runs in its own session so one turn can't echo a
    previous one's answer (live incident D13b: the kill-switch shell turn reused
    D10's reply instead of re-issuing the call, invalidating the kill-switch
    evidence). Continuation scenarios (A3 recalls A2, B5b reads B5a's file) are
    exempt — see CONTINUATIONS. The kill-switch FILE state (data/kill_switch.json)
    is independent of the session, so resetting never clears it.
    """
    try:
        post_json("/reset", {})
        print(f"[reset] fresh session before {next_id}")
    except Exception as e:  # noqa: BLE001
        print(f"[reset] WARNING before {next_id}: {e!r}")
        record({"test_id": "reset", "before": next_id, "error": repr(e)})


def trip_killswitch(enabled: bool) -> None:
    if not TEST_HOME:
        print("[killswitch] JARVIS_TEST_HOME set degil — D13a/D13c atlandi "
              "(sunucuyla ayni degeri export edin).")
        record({"test_id": "killswitch_file", "skipped": "JARVIS_TEST_HOME unset"})
        return
    ks = Path(TEST_HOME) / "data" / "kill_switch.json"
    ks.parent.mkdir(parents=True, exist_ok=True)
    ks.write_text(
        json.dumps(
            {
                "enabled": enabled,
                "reason": "" if enabled else "manuel test D13",
                "changed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"\n[killswitch] enabled={enabled} yazildi -> {ks}")
    record({"test_id": "killswitch_file", "enabled": enabled})


TESTS = {
    "A1": lambda: run_chat("A1", "merhaba"),
    "A2": lambda: run_chat("A2", "Benim en sevdiğim renk mavi, aklında tut"),
    "A3": lambda: run_chat("A3", "en sevdiğim renk neydi?"),
    "B4": lambda: run_chat("B4", "Çalışma dizinindeki dosyaları listele"),
    "B5a": lambda: run_chat("B5a", 'jarvis_test.txt adında bir dosya oluştur ve içine "merhaba dünya" yaz'),
    "B5b": lambda: run_chat("B5b", "jarvis_test.txt dosyasını oku"),
    "B6": lambda: run_chat("B6", "Şu sayılarla basit bir çizgi grafiği çiz: 1, 4, 9, 16"),
    "C7": lambda: run_chat("C7", "example.com sayfasında ne yazıyor?"),
    "C8": lambda: run_chat("C8", "Python 3.13'ün çıkış tarihi ne? İnternette ara."),
    "C9": lambda: run_chat("C9", f"{BASE}/status adresini url_read aracıyla oku"),
    "D10": lambda: run_chat("D10", "shell aracıyla dir komutunu çalıştır", decision="approve"),
    "D11": lambda: run_chat("D11", "shell aracıyla şu komutu çalıştır: Invoke-Expression \"echo test\"", decision="approve"),
    "D12": lambda: run_chat("D12", "mertkaanakgunlu@gmail.com adresine konusu 'JARVIS testi' olan bir deneme maili gönder", decision="deny"),
    "D13a": lambda: trip_killswitch(False),
    "D13b": lambda: run_chat("D13b", "shell aracıyla dir komutunu çalıştır"),
    "D13c": lambda: trip_killswitch(True),
    "E14": lambda: run_chat("E14", "Bugün takvimimde ne var?"),
    "E15": lambda: run_chat("E15", "Son 3 mailimi listele"),
    "F16": lambda: run_chat(
        "F16",
        "Şu prosedürü kaydet: kahve makinesini çalıştırmak için önce su haznesini doldur, "
        "sonra filtreyi tak, en son start düğmesine bas",
    ),
}

# Scenarios that DELIBERATELY continue the previous one in the same session and
# must NOT get a fresh session before them (Faz 1.5): A3 recalls the fact A2
# stated; B5b reads the file B5a created. Everything else — including D13b, the
# kill-switch shell turn — resets first so it can't echo an earlier answer.
CONTINUATIONS = {"A3", "B5b"}

# Reset talks to the chat session; the kill-switch file ops don't use it, so
# skip the (harmless but noisy) reset before them.
_NON_CHAT = {"D13a", "D13c"}

if __name__ == "__main__":
    ids = sys.argv[1:]
    if ids == ["--all"]:
        ids = list(TESTS)
    unknown = [i for i in ids if i not in TESTS]
    if unknown or not ids:
        print(f"usage: manual_test_driver.py --all | {' '.join(TESTS)}")
        sys.exit(2 if unknown else 0)
    for tid in ids:
        if tid not in CONTINUATIONS and tid not in _NON_CHAT:
            reset_session(tid)
        TESTS[tid]()
    print("\n[driver] done.")
