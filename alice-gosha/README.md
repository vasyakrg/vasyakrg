# Навык Алисы «Гоша»

Поиск в интернете через openrusrouter.ru (Perplexity Sonar). Python 3.12, без зависимостей.

Perplexity отвечает 3–10 с, а Алиса ждёт ~3 с, поэтому функция запускает сама себя асинхронно,
результат кладёт в Object Storage. Если ответ успел за 2.5 с — озвучивается сразу,
иначе Гоша просит сказать «готово».

## Развёртывание

1. **Бакет** Object Storage, например `gosha-state` (приватный).
2. **Сервисный аккаунт** `gosha-sa` с ролями на каталог:
   `storage.editor`, `functions.functionInvoker`.
3. **Функция** `gosha`, среда `python312`, точка входа `index.handler`, таймаут `60s`, память `128 МБ`,
   сервисный аккаунт `gosha-sa`, код — `index.py`.
4. **Асинхронный вызов**: в настройках версии функции включить «Асинхронный вызов»,
   сервисный аккаунт `gosha-sa`, ретраев — 0.
5. **Переменные окружения**:

   | Переменная    | Значение                                              |
   |---------------|-------------------------------------------------------|
   | `LLM_KEY`     | ключ openrusrouter.ru (лучше через Lockbox-секрет)    |
   | `LLM_URL`     | `https://openrusrouter.ru/api/v1/chat/completions`    |
   | `LLM_MODEL`   | `perplexity/sonar` (или `perplexity/sonar-pro`)       |
   | `BUCKET`      | `gosha-state`                                         |
   | `FUNCTION_ID` | ID этой функции                                       |

6. **Яндекс Диалоги** → навык «Гоша» → Backend: функция в Яндекс Облаке → `gosha`.
   Активация: «Алиса, запусти навык Гоша».

## CLI

```bash
yc iam service-account create gosha-sa
SA=$(yc iam service-account get gosha-sa --format json | jq -r .id)
FOLDER=$(yc config get folder-id)
for r in storage.editor functions.functionInvoker; do
  yc resource-manager folder add-access-binding $FOLDER --role $r --subject serviceAccount:$SA
done
yc storage bucket create --name gosha-state

yc serverless function create --name gosha
FID=$(yc serverless function get gosha --format json | jq -r .id)
yc serverless function version create --function-name gosha \
  --runtime python312 --entrypoint index.handler --memory 128m --execution-timeout 60s \
  --service-account-id $SA --source-path ./alice-gosha/index.py \
  --async-service-account-id $SA --async-max-retries 0 \
  --environment LLM_KEY=...,LLM_MODEL=perplexity/sonar,BUCKET=gosha-state,FUNCTION_ID=$FID,LLM_URL=https://openrusrouter.ru/api/v1/chat/completions
```

## Фразы

- «сводка мировых новостей за сегодня» — поиск
- «готово» / «что нашёл» — результат
- «повтори» — повторить последний ответ
- «хватит» — выход
