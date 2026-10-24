import json
import os
import re
import time
import urllib.error
import urllib.request

LLM_URL = os.getenv("LLM_URL", "https://openrusrouter.ru/api/v1/chat/completions")
LLM_KEY = os.environ["LLM_KEY"]
LLM_MODEL = os.getenv("LLM_MODEL", "perplexity/sonar")
BUCKET = os.environ["BUCKET"]
FUNCTION_ID = os.environ["FUNCTION_ID"]

ALICE_WAIT = 2.5      # сколько ждём ответ внутри запроса Алисы (лимит Алисы ~3 c)
RESULT_TTL = 600      # результат считается актуальным 10 минут

SYSTEM_PROMPT = (
    "Ты Гоша — голосовой помощник в Яндекс Алисе. Ищи свежие данные в интернете "
    "и отвечай по-русски, кратко и по делу, обычным текстом для озвучивания: "
    "без markdown, списков со звёздочками, ссылок и номеров источников. "
    "Не больше 800 символов. Если просят новости — назови 4-6 главных событий "
    "с учётом сегодняшней даты."
)

RESULT_WORDS = re.compile(r"\b(готов|что наш|ну что|результат|расскажи|покажи|нашел|нашёл)", re.I)
REPEAT_WORDS = re.compile(r"\b(повтори|еще раз|ещё раз)", re.I)
EXIT_WORDS = re.compile(r"\b(хватит|стоп|выход|пока|до свидания)\b", re.I)


# ---------- Object Storage (авторизация IAM-токеном сервисного аккаунта функции) ----------

def _s3(method, key, token, body=None):
    req = urllib.request.Request(
        f"https://storage.yandexcloud.net/{BUCKET}/{key}",
        data=body, method=method,
        headers={"X-YaCloud-SubjectToken": token, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def save(uid, data, token):
    data["ts"] = time.time()
    _s3("PUT", f"gosha/{uid}.json", token, json.dumps(data, ensure_ascii=False).encode())


def load(uid, token):
    raw = _s3("GET", f"gosha/{uid}.json", token)
    if not raw:
        return None
    data = json.loads(raw)
    return data if time.time() - data.get("ts", 0) < RESULT_TTL else None


# ---------- LLM ----------

def ask_llm(query):
    body = json.dumps({
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Сегодня {time.strftime('%d.%m.%Y')}. {query}"},
        ],
        "max_tokens": 600,
    }).encode()
    req = urllib.request.Request(LLM_URL, data=body, method="POST", headers={
        "Authorization": f"Bearer {LLM_KEY}",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=60) as r:
        text = json.load(r)["choices"][0]["message"]["content"]
    return clean(text)


def clean(text):
    text = re.sub(r"\s*\[\d+(?:,\s*\d+)*\]", "", text)  # сноски [1]
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[*#_`>]+", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text).strip()
    return text[:1000]


# ---------- асинхронный запуск самой себя ----------

def start_worker(uid, query, token):
    req = urllib.request.Request(
        f"https://functions.yandexcloud.net/{FUNCTION_ID}?integration=async",
        data=json.dumps({"worker": True, "uid": uid, "query": query}, ensure_ascii=False).encode(),
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    urllib.request.urlopen(req, timeout=2).read()


def run_worker(event, token):
    uid, query = event["uid"], event["query"]
    try:
        answer = ask_llm(query)
        save(uid, {"status": "done", "query": query, "answer": answer}, token)
    except Exception as e:
        print("worker error:", repr(e))
        save(uid, {"status": "error", "query": query}, token)


# ---------- Алиса ----------

def reply(event, text, end=False):
    return {
        "version": event["version"],
        "session": event["session"],
        "response": {"text": text[:1024], "tts": text[:1024], "end_session": end},
    }


def from_state(event, st):
    if st is None:
        return reply(event, "Я пока ничего не искал. Что найти?")
    if st["status"] == "pending":
        return reply(event, "Ещё ищу. Скажите «готово» через пару секунд.")
    if st["status"] == "error":
        return reply(event, "Не получилось найти, попробуйте спросить ещё раз.")
    return reply(event, st["answer"] + "\nЧто ещё найти?")


def handler(event, context):
    token = context.token["access_token"]

    if event.get("worker"):
        run_worker(event, token)
        return {"ok": True}

    req = event["request"]
    utt = (req.get("original_utterance") or req.get("command") or "").strip()
    session = event["session"]
    uid = (session.get("user") or {}).get("user_id") or session["application"]["application_id"]

    if not utt:
        return reply(event, "Привет, я Гоша. Могу поискать в интернете что угодно. "
                            "Например: сводка мировых новостей за сегодня.")
    if EXIT_WORDS.search(utt):
        return reply(event, "До связи!", end=True)
    if RESULT_WORDS.search(utt) and len(utt.split()) <= 4 or REPEAT_WORDS.search(utt):
        return from_state(event, load(uid, token))

    save(uid, {"status": "pending", "query": utt}, token)
    start_worker(uid, utt, token)

    deadline = time.time() + ALICE_WAIT
    while time.time() < deadline:
        time.sleep(0.4)
        st = load(uid, token)
        if st and st["status"] != "pending":
            return from_state(event, st)

    return reply(event, "Ищу. Скажите «готово» через несколько секунд.")
