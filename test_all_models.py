#!/usr/bin/env python3
"""
test_all_models.py — Task 3
Comprehensively test every text-output Pollinations model for RUSSIAN quality.

Runs two tests per model:
  Test A — chat/quality (Russian + contacts check, max_tokens=400, T=0.7)
  Test B — comment/brief (short Russian, max_tokens=150, T=0.85)

Categorizes each model:
  WORKS_RU_GOOD — 200 OK, RU quality 4-5 on BOTH tests, non-empty
  WORKS_RU_OK   — 200 OK, RU quality 3-4 (usable as fallback)
  WORKS_RU_POOR — 200 OK but poor RU (<=2) or English-only
  FAIL_402      — Payment Required (premium)
  FAIL_400      — Invalid model
  FAIL_EMPTY    — 200 OK but empty content
  FAIL_OTHER    — timeout, 429, 500, etc.

Saves results incrementally to /home/z/my-project/model_test_results.json
after each model (so it survives interruptions).
Supports CLI args to test only specified models, and a --resume flag.
"""

import json
import re
import sys
import time
import datetime
import os
import httpx

API_KEY = "sk_Zi7ULzl8uWy8yOjFubmhYeJvwAdltpOs"
AUTH_URL = "https://gen.pollinations.ai/v1/chat/completions"
MODELS_URL = "https://gen.pollinations.ai/v1/models"
RESULTS_JSON = "/home/z/my-project/model_test_results.json"
PROGRESS_JSONL = "/home/z/asdasha/model_progress.jsonl"
TIMEOUT = 60.0
SLEEP_BETWEEN = 3.0

# Reasoning models — they often emit <think>...</think> blocks or have empty content
REASONING_MODELS = {
    "gpt-5.4", "deepseek", "deepseek-pro", "grok-4-20-reasoning", "grok-large",
    "kimi", "kimi-code", "nova", "glm", "minimax-m2.7", "minimax",
    "mistral", "mistral-large", "gemma", "perplexity-reasoning",
    "step-flash", "step-3.5-flash", "qwen-large", "qwen-vision-pro",
    "polly", "openai-large",
}

SYSTEM_A = "Ты — Даша, дизайнер мебели из Абакана. Отвечай только на русском языке, живо и дружелюбно."
USER_A = "Привет! Хочу заказать кухню. Подскажешь по ценам и как связаться?"

SYSTEM_B = "Ты — Даша, дизайнер мебели. Ответь КРАТКО на русском (1-3 предложения), живо."
USER_B = "Крутая кухня!"

# Contact patterns
PHONE_PATTERNS = [
    r"\+?\s*7\s*[\(\s\-]?\s*913\s*[\)\s\-]?\s*448\s*[\s\-]?\s*37[\s\-]?17",
    r"7\s*913\s*448\s*37\s*17",
    r"79134483717",
    r"8\s*[\(\s\-]?\s*913\s*[\)\s\-]?\s*448\s*[\s\-]?\s*37[\s\-]?17",
]
SITE_PATTERN = r"abakanmebel\.online"
WHATSAPP_PATTERN = r"wa\.me/79134483717"

THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)

EXCLUDE_MODELS = {
    "openai-audio", "openai-audio-large",
    "midijourney", "midijourney-large",
    "qwen-safety",
    "whisper",
    "universal-2", "universal-3-pro",
    "gpt-realtime-2",
}


def log(msg):
    print(msg, flush=True)


def get_text_models():
    """Return list of pure text-output model IDs from the API."""
    headers = {"Authorization": f"Bearer {API_KEY}"}
    with httpx.Client(timeout=30.0, headers=headers) as cli:
        r = cli.get(MODELS_URL)
        r.raise_for_status()
        d = r.json()
    models = []
    for m in d.get("data", []):
        mods = m.get("output_modalities", [])
        mid = m["id"]
        if "text" in mods and mid not in EXCLUDE_MODELS:
            if "audio" in mods or "image" in mods or "video" in mods:
                continue
            models.append(mid)
    return models


def extract_answer(content):
    if content is None:
        return ""
    s = content
    s = THINK_RE.sub("", s)
    if "<think>" in s and "</think>" not in s:
        idx = s.find("<think>")
        if idx > 0:
            s = s[:idx]
    return s.strip()


def count_cyrillic(s):
    return sum(1 for c in s if "\u0400" <= c <= "\u04ff")


def count_latin_words(s):
    words = re.findall(r"[A-Za-z]+", s)
    return len([w for w in words if len(w) >= 2])


def score_russian_quality(text, expected_contacts=False):
    if not text or len(text.strip()) < 5:
        return 1
    text = text.strip()
    cyr = count_cyrillic(text)
    lat_words = count_latin_words(text)
    total_alpha = cyr + sum(len(w) for w in re.findall(r"[A-Za-z]+", text))
    if cyr < 10:
        return 1
    cyr_ratio = cyr / max(1, total_alpha)
    if cyr_ratio < 0.55:
        return 2
    if cyr_ratio < 0.80 or lat_words >= 6:
        return 3
    if len(text) < 30:
        return 5 if cyr_ratio > 0.85 else 4
    if cyr_ratio > 0.92 and lat_words <= 3:
        return 5
    if cyr_ratio > 0.85:
        return 4
    return 3


def contacts_present(text):
    t = text.lower()
    for pat in PHONE_PATTERNS:
        if re.search(pat, text, re.IGNORECASE):
            return True
    if re.search(SITE_PATTERN, t):
        return True
    if re.search(WHATSAPP_PATTERN, t):
        return True
    return False


# Use a single shared client to avoid connection leaks.
_CLIENT = None
def get_client():
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = httpx.Client(timeout=TIMEOUT)
    return _CLIENT


def call_model(model, system, user, max_tokens, temperature):
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    t0 = time.time()
    try:
        cli = get_client()
        r = cli.post(AUTH_URL, json=payload, headers=headers, timeout=TIMEOUT)
        latency = round(time.time() - t0, 2)
        status = r.status_code
        try:
            body = r.json()
        except Exception:
            body = {"_raw": r.text[:500]}
        if status == 200:
            try:
                msg = body["choices"][0]["message"]
                content = msg.get("content")
                reasoning = msg.get("reasoning") or msg.get("reasoning_content")
                answer = extract_answer(content)
                leak = False
                if not answer and reasoning:
                    leak = True
                    answer = extract_answer(reasoning)
                return {
                    "status": 200,
                    "latency": latency,
                    "content_raw": (content or "")[:250],
                    "answer": answer[:1500],
                    "answer_full_len": len(answer),
                    "reasoning_leak": leak,
                    "error": None,
                }
            except Exception as e:
                return {
                    "status": 200,
                    "latency": latency,
                    "content_raw": "",
                    "answer": "",
                    "answer_full_len": 0,
                    "reasoning_leak": False,
                    "error": f"Parse error: {e}; body={str(body)[:200]}",
                }
        else:
            err = ""
            if isinstance(body, dict):
                err = body.get("error", {}).get("message") if isinstance(body.get("error"), dict) else body.get("error")
                if not err:
                    err = body.get("message", "")
            err = str(err)[:200] if err else r.text[:200]
            return {
                "status": status,
                "latency": latency,
                "content_raw": "",
                "answer": "",
                "answer_full_len": 0,
                "reasoning_leak": False,
                "error": err,
            }
    except httpx.TimeoutException:
        return {
            "status": 0,
            "latency": round(time.time() - t0, 2),
            "content_raw": "",
            "answer": "",
            "answer_full_len": 0,
            "reasoning_leak": False,
            "error": "Timeout",
        }
    except Exception as e:
        return {
            "status": 0,
            "latency": round(time.time() - t0, 2),
            "content_raw": "",
            "answer": "",
            "answer_full_len": 0,
            "reasoning_leak": False,
            "error": f"Exception: {e}"[:200],
        }


def categorize(a, b):
    a_fail = a["status"] != 200
    b_fail = b["status"] != 200
    if a_fail or b_fail:
        for r in (a, b):
            if r["status"] == 402:
                return "FAIL_402"
            if r["status"] == 400:
                return "FAIL_400"
        for r in (a, b):
            if r["status"] == 0 and "Timeout" in (r["error"] or ""):
                return "FAIL_OTHER"
            if r["status"] in (429, 500, 502, 503, 504):
                return "FAIL_OTHER"
        return "FAIL_OTHER"
    a_empty = (a["answer_full_len"] == 0)
    b_empty = (b["answer_full_len"] == 0)
    if a_empty or b_empty:
        return "FAIL_EMPTY"
    qa = score_russian_quality(a["answer"], expected_contacts=True)
    qb = score_russian_quality(b["answer"])
    min_q = min(qa, qb)
    avg_q = (qa + qb) / 2
    if min_q >= 4:
        return "WORKS_RU_GOOD"
    if avg_q >= 3 and min_q >= 2:
        return "WORKS_RU_OK"
    if min_q <= 2:
        return "WORKS_RU_POOR"
    return "WORKS_RU_OK"


def load_progress():
    """Load previously-tested models from the JSONL progress file."""
    done = {}
    if os.path.exists(PROGRESS_JSONL):
        with open(PROGRESS_JSONL, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    done[rec["model"]] = rec
                except Exception:
                    pass
    return done


def save_progress_record(rec):
    with open(PROGRESS_JSONL, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        f.flush()


def write_final_json(results):
    out = {
        "results": results,
        "timestamp": datetime.datetime.now().isoformat(),
        "total_models": len(results),
    }
    tmp = RESULTS_JSON + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    os.replace(tmp, RESULTS_JSON)


def test_one_model(model):
    is_reasoning = model in REASONING_MODELS
    log(f"  reasoning model: {is_reasoning}")

    log(f"  Test A (chat/quality)...", )
    a = call_model(model, SYSTEM_A, USER_A, max_tokens=400, temperature=0.7)
    a["is_reasoning"] = is_reasoning
    if a["status"] == 200 and a["answer_full_len"] > 0:
        qa = score_russian_quality(a["answer"], expected_contacts=True)
        a["ru_quality"] = qa
        a["contacts_present"] = contacts_present(a["answer"])
        log(f"    status=200  latency={a['latency']}s  ru_q={qa}  contacts={a['contacts_present']}  len={a['answer_full_len']}")
        log(f"    answer: {a['answer'][:160]!r}")
    else:
        a["ru_quality"] = 0
        a["contacts_present"] = False
        log(f"    status={a['status']}  latency={a['latency']}s  error={a['error']}")
    time.sleep(SLEEP_BETWEEN)

    log(f"  Test B (comment/brief)...")
    b = call_model(model, SYSTEM_B, USER_B, max_tokens=150, temperature=0.85)
    b["is_reasoning"] = is_reasoning
    if b["status"] == 200 and b["answer_full_len"] > 0:
        qb = score_russian_quality(b["answer"])
        b["ru_quality"] = qb
        b["contacts_present"] = None
        log(f"    status=200  latency={b['latency']}s  ru_q={qb}  len={b['answer_full_len']}")
        log(f"    answer: {b['answer'][:160]!r}")
    else:
        b["ru_quality"] = 0
        b["contacts_present"] = None
        log(f"    status={b['status']}  latency={b['latency']}s  error={b['error']}")
    time.sleep(SLEEP_BETWEEN)

    cat = categorize(a, b)
    ru_q = min(a["ru_quality"], b["ru_quality"]) if (a["ru_quality"] and b["ru_quality"]) else (a["ru_quality"] or b["ru_quality"])
    avg_lat = round((a["latency"] + b["latency"]) / 2, 2)
    log(f"  -> category: {cat}  ru_q_min: {ru_q}  avg_latency: {avg_lat}s")

    return {
        "model": model,
        "is_reasoning": is_reasoning,
        "test_a": {
            "status": a["status"],
            "latency": a["latency"],
            "error": a["error"],
            "ru_quality": a["ru_quality"],
            "contacts_present": a["contacts_present"],
            "answer": a["answer"],
            "answer_full_len": a["answer_full_len"],
            "reasoning_leak": a["reasoning_leak"],
        },
        "test_b": {
            "status": b["status"],
            "latency": b["latency"],
            "error": b["error"],
            "ru_quality": b["ru_quality"],
            "answer": b["answer"],
            "answer_full_len": b["answer_full_len"],
            "reasoning_leak": b["reasoning_leak"],
        },
        "category": cat,
        "ru_quality": ru_q,
        "latency_a": a["latency"],
        "latency_b": b["latency"],
        "avg_latency": avg_lat,
        "contacts_present": a["contacts_present"],
    }


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="*", default=None,
                    help="Specific model IDs to test (default: all text models from API)")
    ap.add_argument("--resume", action="store_true",
                    help="Skip models already in progress file")
    ap.add_argument("--list-only", action="store_true",
                    help="Just list models that would be tested and exit")
    args = ap.parse_args()

    log("=" * 70)
    log("TASK 3 — Pollinations Russian text-output model comprehensive test")
    log("=" * 70)
    log(f"Started: {datetime.datetime.now().isoformat()}")
    log("")

    if args.models:
        models = args.models
        log(f"Using CLI-specified models ({len(models)}):")
    else:
        log("Fetching model list...")
        try:
            models = get_text_models()
        except Exception as e:
            log(f"ERROR fetching models: {e}")
            sys.exit(1)
        log(f"Got {len(models)} text-output models.")
    log("")
    for m in models:
        log(f"  - {m}")
    log("")

    if args.list_only:
        return

    done = load_progress() if args.resume else {}
    if done:
        log(f"Resume mode: {len(done)} models already tested, will skip them.")

    results = list(done.values())  # carry forward previous results
    todo = [m for m in models if m not in done]

    log(f"To test now: {len(todo)} models. Already done: {len(done)}")
    log("")

    for i, model in enumerate(todo, 1):
        log(f"[{i}/{len(todo)}] === {model} ===")
        try:
            rec = test_one_model(model)
        except KeyboardInterrupt:
            log("Interrupted!")
            break
        except Exception as e:
            log(f"  FATAL error testing {model}: {e}")
            rec = {
                "model": model,
                "is_reasoning": model in REASONING_MODELS,
                "test_a": {"status": 0, "latency": 0, "error": f"FATAL: {e}", "ru_quality": 0,
                           "contacts_present": False, "answer": "", "answer_full_len": 0, "reasoning_leak": False},
                "test_b": {"status": 0, "latency": 0, "error": f"FATAL: {e}", "ru_quality": 0,
                           "answer": "", "answer_full_len": 0, "reasoning_leak": False},
                "category": "FAIL_OTHER",
                "ru_quality": 0,
                "latency_a": 0, "latency_b": 0, "avg_latency": 0,
                "contacts_present": False,
            }
        # Remove from results if already present (shouldn't happen with resume)
        results = [r for r in results if r["model"] != model]
        results.append(rec)
        save_progress_record(rec)
        # Re-write the final JSON after each model (atomic)
        write_final_json(results)
        log("")

    # Sort results by model name for the final summary
    results = sorted(results, key=lambda r: r["model"])
    write_final_json(results)
    log(f"Results written to {RESULTS_JSON}")
    log("")

    # ---- Summary ----
    good = [r for r in results if r["category"] == "WORKS_RU_GOOD"]
    ok = [r for r in results if r["category"] == "WORKS_RU_OK"]
    poor = [r for r in results if r["category"] == "WORKS_RU_POOR"]
    f402 = [r for r in results if r["category"] == "FAIL_402"]
    f400 = [r for r in results if r["category"] == "FAIL_400"]
    fempty = [r for r in results if r["category"] == "FAIL_EMPTY"]
    fother = [r for r in results if r["category"] == "FAIL_OTHER"]

    good_sorted = sorted(good, key=lambda r: (r["avg_latency"], -r["ru_quality"]))
    ok_sorted = sorted(ok, key=lambda r: (r["avg_latency"], -r["ru_quality"]))

    log("=" * 70)
    log("SUMMARY")
    log("=" * 70)
    log(f"Total tested: {len(results)}")
    log(f"  WORKS_RU_GOOD : {len(good)}")
    log(f"  WORKS_RU_OK   : {len(ok)}")
    log(f"  WORKS_RU_POOR : {len(poor)}")
    log(f"  FAIL_402      : {len(f402)}")
    log(f"  FAIL_400      : {len(f400)}")
    log(f"  FAIL_EMPTY    : {len(fempty)}")
    log(f"  FAIL_OTHER    : {len(fother)}")
    log("")

    log(">>> WORKS_RU_GOOD (sorted by latency, then quality desc) <<<")
    log(f"{'MODEL':<28} {'RU_Q':>4} {'LAT_A':>6} {'LAT_B':>6} {'CTC':>4} {'REAS':>5}")
    for r in good_sorted:
        log(f"{r['model']:<28} {r['ru_quality']:>4} {r['latency_a']:>6.2f} {r['latency_b']:>6.2f} {('Y' if r['contacts_present'] else 'N'):>4} {('Y' if r['is_reasoning'] else 'N'):>5}")
    log("")

    log(">>> WORKS_RU_OK <<<")
    log(f"{'MODEL':<28} {'RU_Q':>4} {'LAT_A':>6} {'LAT_B':>6} {'CTC':>4} {'REAS':>5}")
    for r in ok_sorted:
        log(f"{r['model']:<28} {r['ru_quality']:>4} {r['latency_a']:>6.2f} {r['latency_b']:>6.2f} {('Y' if r['contacts_present'] else 'N'):>4} {('Y' if r['is_reasoning'] else 'N'):>5}")
    log("")

    if poor:
        log(">>> WORKS_RU_POOR <<<")
        for r in poor:
            log(f"  {r['model']:<28} ru_q={r['ru_quality']}  lat={r['avg_latency']}")
        log("")

    log(">>> FAILED MODELS <<<")
    for r in f402 + f400 + fempty + fother:
        err_a = r['test_a']['error'] or ''
        err_b = r['test_b']['error'] or ''
        log(f"  {r['model']:<28} {r['category']:<12} A: {r['test_a']['status']} {err_a[:60]!r} | B: {r['test_b']['status']} {err_b[:60]!r}")
    log("")

    bot_has = {"gpt-5.4-mini", "nova-fast", "minimax", "minimax-m2.7", "nova",
               "perplexity-fast", "step-3.5-flash", "grok-large", "mistral-small-3.2",
               "llama-scout", "mistral", "grok", "openai", "mistral-small",
               "mistral-large", "llama", "deepseek", "qwen-coder", "gemma"}
    log(">>> NEW working models NOT in bot <<<")
    for r in good_sorted + ok_sorted:
        if r["model"] not in bot_has:
            log(f"  {r['model']:<28} cat={r['category']}  ru_q={r['ru_quality']}  avg_lat={r['avg_latency']}s")
    log("")

    log("=" * 70)
    log("RECOMMENDED ORDERINGS")
    log("=" * 70)
    chat_order = [r["model"] for r in good_sorted] + [r["model"] for r in ok_sorted]
    log(f"CHAT route ({len(chat_order)} models, fastest good first, then ok):")
    for m in chat_order:
        log(f"  {m}")
    log("")

    func_candidates = [r for r in good_sorted if r["test_a"]["answer_full_len"] >= 100]
    if not func_candidates:
        func_candidates = good_sorted
    func_order = [r["model"] for r in func_candidates]
    log(f"FUNCTION route ({len(func_order)} models, best long structured Russian):")
    for m in func_order:
        r = next(x for x in results if x["model"] == m)
        log(f"  {m:<28} ru_q={r['ru_quality']}  test_a_len={r['test_a']['answer_full_len']}  lat={r['avg_latency']}s")
    log("")

    comment_candidates = sorted(
        [r for r in good + ok if r["test_b"]["ru_quality"] >= 3 and r["test_b"]["answer_full_len"] >= 10],
        key=lambda r: (r["latency_b"], -r["test_b"]["ru_quality"])
    )
    comment_order = [r["model"] for r in comment_candidates]
    log(f"COMMENT route ({len(comment_order)} models, fastest decent Russian, sorted by Test B latency):")
    for m in comment_order:
        r = next(x for x in results if x["model"] == m)
        log(f"  {m:<28} ru_q_b={r['test_b']['ru_quality']}  lat_b={r['latency_b']}s  cat={r['category']}")
    log("")

    log("DONE.")


if __name__ == "__main__":
    main()
