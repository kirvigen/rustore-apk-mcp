<p align="center">
  <img src="assets/banner.png" alt="rustore-apk-mcp — проверенные Android APK для AI-агента по одному имени пакета" width="100%">
</p>

<h1 align="center">rustore-apk-mcp</h1>

<p align="center">
  <b>Агент называет имя пакета — и получает APK с проверенной подписью на диске.</b><br>
  Без браузера. Без аккаунта. Без API-ключа.
</p>

<p align="center">
  <a href="https://github.com/kirvigen/rustore-apk-mcp/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/kirvigen/rustore-apk-mcp/actions/workflows/ci.yml/badge.svg"></a>
  <a href="README.md"><img alt="English" src="https://img.shields.io/badge/lang-English-lightgrey"></a>
  <img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white">
  <img alt="MCP" src="https://img.shields.io/badge/MCP-stdio%20сервер-39D98A">
  <a href="LICENSE"><img alt="MIT" src="https://img.shields.io/badge/license-MIT-blue"></a>
</p>

---

Достать APK в рабочую папку агента обычно муторно: найти карточку, пробиться через
страницу загрузки, наткнуться на зеркало неизвестного происхождения — и потом
*надеяться*, что это действительно то приложение. Этот MCP-сервер убирает весь крюк.

```
вы    ▸ Скачай APK для ru.foodfox.client и скажи, где он лежит.

агент ▸ download_apk(package_name="ru.foodfox.client")
       ↳ /Users/you/Downloads/rustore-apks/ru.foodfox.client-250000235.apk
         версия 25.0.0 (250000235) · 84.1 МБ
         sha256  b9f4…c1a7
         проверено: CRC архива, packageName/versionCode из манифеста
                    и подпись APK; сертификат совпал с RuStore
```

Один вызов инструмента. Настоящий файл. Цепочка проверок, на которую можно показать пальцем.

## Зачем это нужно

| | Обычное зеркало APK | `rustore-apk-mcp` |
|---|---|---|
| **Происхождение** | «доверься зеркалу» | только API и CDN самого RuStore |
| **Целостность** | в лучшем случае контрольная сумма | CRC архива + манифест + **подпись APK** |
| **Идентичность** | так написано в имени файла | `packageName` и `versionCode` читаются из манифеста |
| **Подлинность** | — | сертификат подписи **сверяется** с SHA-256 из RuStore |
| **Авторизация** | логины, капчи | не нужна — ни аккаунта, ни ключа |
| **Механика** | headless-браузер, хрупко | обычные HTTPS-запросы |
| **Для агента** | скрипты-прослойки | штатные MCP-инструменты со схемами вывода |

## Быстрый старт

**Нужны:** Python 3.11+, [uv](https://docs.astral.sh/uv/), Java и Android SDK Build Tools
(`aapt`, `apksigner`). Инструменты ищутся в `PATH`, `ANDROID_HOME`, `ANDROID_SDK_ROOT` и
стандартных каталогах SDK на macOS и Linux.

```sh
git clone https://github.com/kirvigen/rustore-apk-mcp.git
cd rustore-apk-mcp
uv sync --locked
uv run --locked rustore-apk-mcp
```

Сервер работает по **stdio**, поэтому отсутствие приглашения после запуска — норма:
`stdout` занят протоколом MCP.

### Подключение к агенту

<details open>
<summary><b>Codex CLI</b></summary>

```sh
codex mcp add rustore-apk \
  --env APK_MCP_DOWNLOAD_DIR="$PWD/downloads/mcp" \
  -- "$PWD/.venv/bin/rustore-apk-mcp"
```

В секцию `[mcp_servers.rustore-apk]` конфигурации Codex добавьте `tool_timeout_sec = 600` —
большим APK плюс проверке подписи нужен запас по времени.
</details>

<details>
<summary><b>Claude Code</b></summary>

```sh
claude mcp add rustore-apk \
  --env APK_MCP_DOWNLOAD_DIR="$PWD/downloads/mcp" \
  -- "$PWD/.venv/bin/rustore-apk-mcp"
```
</details>

<details>
<summary><b>Любой другой MCP-клиент</b></summary>

```json
{
  "mcpServers": {
    "rustore-apk": {
      "command": "/absolute/path/to/rustore-apk-mcp/.venv/bin/rustore-apk-mcp",
      "env": {
        "APK_MCP_DOWNLOAD_DIR": "/absolute/path/to/downloads"
      }
    }
  }
}
```

Таймаут вызова у клиента — **не меньше 600 секунд**.
</details>

Дальше новому агенту достаточно запроса:

> *«Скачай APK `com.example.app` через rustore-apk и верни путь к файлу».*

## Инструменты

### `get_apk_info(package_name)`
Метаданные последнего релиза: название, версия и versionCode, минимальный Android SDK,
цена, сертификат подписи, ссылка на карточку. Только чтение, без скачивания.

### `download_apk(package_name, max_mib=1024)`
Скачивает и проверяет последний бесплатный standalone-APK, затем возвращает:

```json
{
  "package_name": "com.example.app",
  "version_name": "1.4.2",
  "version_code": 10402,
  "path": "/abs/path/com.example.app-10402.apk",
  "bytes": 44236800,
  "sha256": "…",
  "certificate_sha256": "…",
  "verification": "ZIP CRC, manifest package/version and APK signature verified; certificate matches RuStore",
  "cached": false
}
```

`download_apk` можно вызывать сразу — предварительный `get_apk_info` не обязателен.

## Что именно проверяется

Каждый байт проверяется **до** того, как файл встанет на место. Загрузка, провалившая
любой шаг, не превратится в `.apk` у вас в каталоге.

1. **Пиннинг хоста** — URL CDN обязан быть `https://static.rustore.ru`; редиректы не следуются.
2. **Заявленный размер** — поток обрывается, как только превышает обещанное API.
3. **Целостность архива** — CRC каждой записи, с ограничением распаковки в 4 ГиБ против zip-бомб.
4. **Идентичность** — `packageName` и `versionCode` читаются из `AndroidManifest.xml` через
   `aapt` и должны совпасть с запрошенным релизом.
5. **Подпись** — `apksigner verify` обязан пройти.
6. **Пиннинг сертификата** — SHA-256 сертификата подписи должен совпасть с тем, что
   RuStore отдаёт для этой карточки.

Кэш перепроверяется тем же способом при каждом вызове; повреждённый или несовпавший файл
молча скачивается заново. Незавершённые файлы удаляются при любой ошибке.

> [!IMPORTANT]
> Проверка подписи доказывает, что файл **целый и от того же издателя, которого указывает
> RuStore**. Это *не* анализ поведения и не проверка на вредоносность. Сервер никогда не
> устанавливает и не запускает то, что скачал.

## Ограничения (честный список)

- Только последняя версия, истории версий нет.
- Только бесплатные приложения — ненулевая цена отклоняется сразу.
- Только единый APK; split APK / бандлы пока не поддерживаются.
- Карточки с внешним источником отклоняются, а не «додумываются».
- Покрытие — ровно то, что есть в каталоге RuStore.
- Профиль загрузки фиксирован: Android SDK 36, `arm64-v8a`, 480 dpi, `withoutSplits=true`.
- Потребительский API RuStore не является стабильным публичным контрактом и может меняться.
- HTTP 403/429 останавливает вызов сразу, без повторов — пробуйте позже.
- Жёсткие потолки: 5 минут на загрузку, `max_mib` байт, 4 ГиБ распакованных данных на проверку.

## Настройки

| Переменная | По умолчанию |
|---|---|
| `APK_MCP_DOWNLOAD_DIR` | `~/Downloads/rustore-apks` |
| `APK_MCP_AAPT` | автопоиск `aapt` |
| `APK_MCP_APKSIGNER` | автопоиск `apksigner` |
| `RUSTORE_VERSION_CODE` | `1000` (заголовок `ruStoreVerCode`) |

Имена файлов: `<package_name>-<version_code>.apk`.

## Тесты

```sh
uv run pytest -q

# Явный тест с сетью: метаданные, скачивание и переиспользование кэша
# через настоящий MCP stdio-клиент.
uv run python scripts/smoke_mcp.py com.yolo_price_mobile
```

Последняя сквозная проверка 09.09.2026: `com.yolo_price_mobile` 0.9.55 (611).

## Что ещё лежит в репозитории

`apk_scrapper.cli` — более ранний самостоятельный CLI для разбора страниц APKPure (точка
входа `apk-scrapper`). Он появился до MCP-сервера, сервером не используется и оставлен для
истории: MCP-инструменты ходят только в RuStore.

## Как помочь

Issues и PR приветствуются — самое полезное, что можно добавить, это поддержка split APK и
расширение покрытия каталога. Цепочку проверок ломать нельзя: на диск не попадает ничего,
что не прошло все шаги выше.

## Источники

- [Схема API RuStore](https://gist.github.com/oldnomad/5d38a9ea9b1daf9d82fa4f655b9aebe8)
- [Python SDK для MCP](https://github.com/modelcontextprotocol/python-sdk)
- [Подключение MCP в Codex](https://developers.openai.com/codex/mcp)

## Лицензия

[MIT](LICENSE)

---

<sub>Проект не аффилирован с RuStore и VK и не одобрен ими. Пользуйтесь в соответствии с
условиями RuStore и лицензией скачиваемого приложения.</sub>
