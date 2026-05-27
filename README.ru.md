# LMU PIT WALL

> 🌐 **Язык:** [English](README.md) · **Русский**

> Реальная WEC-style телеметрия для **Le Mans Ultimate** (движок rFactor 2).
> Сделано под эндюранс-гонки — твой удалённый инженер-стратег видит ровно те же данные, что и настоящий pit-wall engineer.

![tech](https://img.shields.io/badge/stack-Python%20·%20FastAPI%20·%20WebSocket-black?style=flat-square)
![engine](https://img.shields.io/badge/engine-rFactor%202-red?style=flat-square)
![platform](https://img.shields.io/badge/platform-Windows-blue?style=flat-square)
![license](https://img.shields.io/badge/license-MIT-green?style=flat-square)

Самохостящаяся альтернатива сервисам типа *mylmu*. Читает секции Shared Memory из rF2 Shared Memory Map Plugin напрямую, стримит компактный JSON-снимок по WebSocket на 50 Гц, рендерит как хардкорный моноширинный дашборд, повторяющий настоящий пит-уолл WEC.

## Возможности

- **Телеметрия в реальном времени @ 50 Гц** — передача, RPM, скорость, газ/тормоз/сцепление, руль
- **4 шины** — внутр/центр/внешн температуры с цветовой картой cold→optimal→hot, давление (PSI + кПа), износ, температура каркаса и тормозов
- **Состояние машины** — топливо (с предупреждениями low/critical), температура воды, масла, давление турбонаддува
- **Timing tower** — позиция, круг, текущий/последний/лучший таймы, полная таблица S1/S2/S3 со столбцом дельт, подсветка session-best (magenta), индикатор активного сектора
- **Strategy assist** — скользящее среднее fuel-per-lap, оценка оставшихся кругов, счётчик пит-стопов и штрафов, gap до впереди/лидера
- **Мультикласс таблица** — все машины поля сгруппированы по классам (Hypercar/LMP2/LMGT3) с цветовой кодировкой, твоя строка подсвечена
- **Карта трассы** — автоматически собирается из координат пилота, все машины — живые точки. Работает на **любом** треке без preset'ов
- **Flag strip** — LIVE / PIT / SPDLIM / OVERHEAT / FUEL! / YELLOW
- **Авто-реконнект** WebSocket с экспоненциальным backoff
- **Режим `--demo`** — работает без LMU, идеально для проверки UI на macOS/Linux
- **Монолитный фронтенд** — без build-шага, без npm, без фреймворков
- **Встроенная справка для стратега** — кнопка `? GUIDE` поверх дашборда

## Архитектура

```
┌──────────────┐   Shared Memory     ┌──────────────┐   WebSocket 50Hz   ┌──────────────┐
│ Le Mans      │ ──────────────────► │  server.py   │ ─────────────────► │  index.html  │
│ Ultimate +   │   $rFactor2SMMP_    │  FastAPI +   │      ws://         │  Vanilla JS  │
│ rF2 SMMP     │     Telemetry$      │  ctypes      │     /ws            │  Моноширинный│
│ Plugin       │     Scoring$        │  парсер      │                    │  дашборд     │
└──────────────┘                     └──────────────┘                    └──────────────┘
                                                            ngrok / cloudflared
                                                                   │
                                                                   ▼
                                                       Браузер стратега где угодно
```

## Что нужно

1. **Le Mans Ultimate** установлена (Windows).
2. **rF2 Shared Memory Map Plugin (TheIronWolfMod)** — обязателен, чтобы существовали секции
   `$rFactor2SMMP_Telemetry$` / `$rFactor2SMMP_Scoring$`:
   - скачать `rF2SharedMemoryMapPlugin64.dll` из оригинального репо
   - положить в `<LMU install>\Bin64\Plugins\`
   - включить плагин в игре (`Settings → Plugins`)
3. **Python 3.10+**.

## Установка

```powershell
git clone https://github.com/NikMusy/le-mans-ultimate-telemetry.git
cd le-mans-ultimate-telemetry

python -m venv .venv
.\.venv\Scripts\Activate.ps1

pip install fastapi "uvicorn[standard]"
```

## Запуск

```powershell
# Демо — синтетические данные, LMU не нужен (отлично для проверки UI)
python server.py --demo

# Боевой — LMU запущена, плагин включён
python server.py

# С параметрами
python server.py --host 0.0.0.0 --port 8000 --hz 60
```

Открой `http://127.0.0.1:8000/` — увидишь полный дашборд. Если LMU в главном меню, UI покажет **AWAITING TELEMETRY**; сервер при этом **не упадёт**.

## Удалённый доступ для стратега

Бэкенд **обязан** работать на гоночном PC — только эта машина имеет доступ к
Shared Memory от LMU. Во время гонки должны крутиться две вещи:

### 1. Локальный Python-сервер + туннель

```powershell
# terminal 1
python server.py

# terminal 2 (cloudflared)
cloudflared tunnel --url http://localhost:8000 --protocol http2
# → https://<random>.trycloudflare.com

# или ngrok
ngrok http 8000
# → https://<random>.ngrok-free.app
```

Для эндюрансов на 24 часа лучше зарезервировать постоянный subdomain, чтобы URL не менялся при перезапуске:

```powershell
ngrok http --domain=your-reserved-name.ngrok-free.app 8000
```

### 2. Фронтенд (хостится на Cloudflare Pages)

Дашборд уже live на **https://lmu-pitwall.pages.dev/**. Стратег открывает этот URL откуда угодно. При первом визите вводит WebSocket-адрес один раз (или ты отправляешь готовую ссылку):

```
https://lmu-pitwall.pages.dev/?ws=wss://your-reserved-name.ngrok-free.app/ws
```

URL запоминается в `localStorage`, перезагрузки/перезапуски работают сами. Кнопка `⚙ CONNECTION` в footer позволяет поменять в любой момент.

### Для стратега

Полная методичка лежит в [**STRATEGIST_GUIDE.ru.md**](STRATEGIST_GUIDE.ru.md). Покрывает раскладку экрана, шпаргалку по цветам шин, цвета лап-таймов, 7 типичных race-сценариев с готовыми формулировками для рации, troubleshooting, шаблон пит-журнала. Отправь инженеру до старта гонки.

Внутри дашборда кнопка `? GUIDE` в footer открывает встроенную сокращённую версию той же шпаргалки — стратегу не надо уходить с экрана во время стинта.

## Деплой (Cloudflare Pages)

После клонирования репо:

```powershell
npm install                # один раз, ставит wrangler локально
npx wrangler login         # один раз, открывает Cloudflare OAuth в браузере
npm run deploy             # деплоит static/ на https://lmu-pitwall.pages.dev
```

Полезные команды:

```powershell
npm run list               # список прошлых деплоев
npm run tail               # live tail логов (HTTP-запросы, ошибки)
npm run deploy:preview     # деплой в preview branch URL
```

### Кастомный домен (опционально)

1. В Cloudflare dashboard: **Workers & Pages → lmu-pitwall → Custom domains → Set up a custom domain**.
2. Введи `pitwall.your-domain.com` (или apex `your-domain.com`).
3. Если DNS домена уже на Cloudflare — всё подцепится автоматически. Если нет — добавь CNAME запись на `lmu-pitwall.pages.dev` у регистратора.

## Раскладка

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ ● LMU PIT WALL  · CKT · CAR · DRV · CLS · AIR/TRK/WET · sessionclock         │
├────────────────────────────┬──────────────────┬──────────────────────────────┤
│           VITALS            │      TIRES       │      TIMING TOWER           │
│   ┌───────────────────┐    │  ┌────┐  ┌────┐  │  ┌──────────┐ ┌──────────┐  │
│   │                   │    │  │ FL │  │ FR │  │  │ POSITION │ │   LAP    │  │
│   │        6          │    │  └────┘  └────┘  │  │    03    │ │    47    │  │
│   │                   │    │  ┌────┐  ┌────┐  │  ├──────────┴─┴──────────┤  │
│   └───────────────────┘    │  │ RL │  │ RR │  │  │ CURR  3:31.423        │  │
│   [████ shift lights]      │  └────┘  └────┘  │  │ LAST  3:30.124        │  │
│                             │                  │  │ BEST  3:28.912        │  │
│   THR  BRK  CLT             ├──────────────────┤  ├──────────────────────────┤
│                             │   CAR STATUS     │  │ S1 / S2 / S3 / Δ       │  │
│   [── STEERING ──]          │  FUEL 84.3L      │  │ ...                    │  │
│                             │  WATER 88° OIL 96│  │ GAPS · FLAGS           │  │
└────────────────────────────┴──────────────────┴──────────────────────────────┘
```

## Структура проекта

```
le-mans-ultimate-telemetry/
├── server.py                 # FastAPI + ctypes SMMP reader + WebSocket стрим
├── static/
│   └── index.html            # Монолитный фронтенд (HTML + CSS + JS)
├── package.json              # npm scripts для wrangler-деплоя
├── wrangler.toml             # Конфиг Cloudflare Pages
├── STRATEGIST_GUIDE.md       # Полный гайд для стратега (английский)
├── STRATEGIST_GUIDE.ru.md    # Полный гайд для стратега (русский)
├── README.md                 # Этот файл (английский)
├── README.ru.md              # Этот файл (русский)
├── LICENSE
└── .gitignore
```

## Тюнинг

| Переменная | Где | По умолчанию | Что контролирует |
|---|---|---|---|
| `STREAM_HZ` (CLI `--hz`) | `server.py` | 50 | Частота кадров WebSocket |
| Пороги `tireColor()` | `index.html` JS | 60 / 80-100 / 115 °C | Cold / optimal / hot цвета шин |
| `fuel low / critical` | `index.html` JS | 25 / 10 L | Когда FUEL card становится оранжевым / мигает красным |
| Порог `ovh` | `index.html` JS | вода > 110 °C или масло > 130 °C | Включает флаг OVHEAT |

## Troubleshooting

- **"AWAITING TELEMETRY" не уходит** — DLL SMMP-плагина не загружена. Проверь путь (`<LMU>\Bin64\Plugins\`) и что LMU его загрузила (`Settings → Plugins` — галочка).
- **`ModuleNotFoundError: No module named 'fastapi'`** — активируй venv, потом `pip install fastapi "uvicorn[standard]"`.
- **Стратег получает "Mixed Content"** — ngrok HTTPS пробрасывает на HTTP-бэкенд, это нормально; фронтенд использует `wss://` и это работает.
- **Неправильные температуры шин** — значения конвертируются из Кельвинов в `_wheel_to_dict()`. Проверь константу `KELVIN`, если показывает на ~273° мимо.
- **Cloudflared не пробивается через QUIC** — добавь флаг `--protocol http2` (TCP/443, обычно не блокируется).

## Авторство

- Layout shared memory основан на [rF2SharedMemoryMapPlugin](https://github.com/TheIronWolfMod/rF2SharedMemoryMapPlugin) от TheIronWolfMod
- Визуальный язык вдохновлён пит-уолл инженерами FIA WEC / F1
- Сделано **NikMusy × Claude Opus 4.7** (Anthropic). От первой строчки кода до production-deploy — одна сессия.
