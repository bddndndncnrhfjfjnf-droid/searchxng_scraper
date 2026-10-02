# Anubis v1.27.0 — разбор челленджера (извлечено с searxng.deggo.fyi через agent-browser)

## Файлы

| Файл | Роль |
|------|------|
| `main.mjs` | Оркестратор: читает challenge из DOM, спавнит Web Workers, отправляет pass-challenge |
| `sha256-webcrypto.mjs` | Воркер: SHA-256 через `crypto.subtle` (Chrome и др. secure context) |
| `sha256-purejs.mjs` | Воркер: чистый JS SHA-256 (fallback для Firefox/Goanna или insecure context) |

## Алгоритм (точное соответствие исходникам)

1. **Challenge из DOM** (`main.mjs`, `x("anubis_challenge")`):
   ```json
   {"rules": {"algorithm": "fast", "difficulty": 5},
    "challenge": {"id": "uuid", "randomData": "<128 hex>", "difficulty": 5, ...}}
   ```
   `algorithm: "fast"` → функция `_` (внутри всё равно выбирает webcrypto/purejs воркер).

2. **PoW-цикл воркера** (`sha256-*.mjs`, слушатель `message`):
   ```js
   for(;;) {
     let f = await sha256(data + nonce);          // data = randomData, строка
     let c = new Uint8Array(f), ok = true;
     let p = Math.floor(difficulty / 2);          // полные нулевые байты
     for (let s = 0; s < p; s++) if (c[s] !== 0) { ok = false; break; }
     if (ok && difficulty % 2 !== 0 && c[p] >> 4 !== 0) ok = false; // полбайта
     if (ok) { postMessage({hash: hex(c), nonce}); return; }
     nonce += threads;                            // threads воркеров делят пространство
   }
   ```
   Т.е. условие: **hex(SHA256(randomData + nonce)) начинается с `difficulty` нулевых
   hex-символов** (чётная difficulty → нулевые байты; нечётная → + старший нибл нуля).
   Python-эквивалент (реализовано в `anubis_solver.py::_solve_pow`):
   ```python
   digest = hashlib.sha256(random_data.encode() + str(nonce).encode()).hexdigest()
   if digest.startswith("0" * difficulty): ...
   ```

3. **Мультипоточность** (`main.mjs`, `V`): `threads = max(hardwareConcurrency/2, 1)`
   воркеров, каждый стартует со сдвигом `nonce = l` и шагом `threads`. Найденное
   решение постит первый успешный воркер.

4. **Отправка** (`main.mjs`, после успеха):
   ```js
   window.location.replace(pass-challenge + {
     id: challenge.id, response: hash, nonce: nonce,
     redir: <redir или "/">, elapsedTime: Date.now() - started   // мс, строка
   })
   ```
   Механизация: браузер просто GET-ает этот URL; сервер ставит куки
   `techaro.lol-anubis-auth-*` (JWT, ~1 час) и редиректит на `redir`.

## Требования клиента, зашитые в main.mjs

- `window.Worker` обязателен — при отсутствии «missing feature: Web Workers».
- `navigator.cookieEnabled` обязателен — без кук pass-challenge отвечает
  500 "Your browser is configured to disable cookies".
- Сервер **не** проверяет отпечаток браузера и время решения (elapsedTime
  записывается только в метрики) — challenge не привязан к UA/IP.

## Вывод для Python-реализации

Все проверки — ровно две: валидная JWT-подпись решения и возврат
cookie-verification куки. Обе выполняются чистым HTTP: сессия с cookie jar
(curl_cffi) + CPU-солв SHA-256. Браузер не нужен.
