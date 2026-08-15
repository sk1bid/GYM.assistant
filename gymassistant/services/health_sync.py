"""
Метрики Apple Health из hae-server → плоские строки для таблицы health_metric.

Здоровье приезжает с телефона в отдельный стек (namespace health-export:
hae-server + MongoDB), а тренировки лежат в нашем Postgres. Смотреть их вместе
неоткуда: Grafana к MongoDB нативно не ходит вовсе, а join между двумя базами
не сделать. Поэтому метрики переносятся к тренировкам, а не наоборот — они
плоский временной ряд и в Postgres ложатся, тогда как связи
`session → set → exercise` в Mongo не переехали бы никак.

ФОРМА ДОКУМЕНТА У МЕТРИК РАЗНАЯ — это главное, что определило схему. Их три:

    step_count      {"date":…, "qty":5200,           "units":"count"}
    heart_rate      {"date":…, "Avg":64,"Min":64,"Max":64, "units":"count/min"}
    sleep_analysis  {"date":…, "deep":0.86,"rem":1.45,"core":4.09,"awake":0.04, …}

Поэтому одной колонки `value` не хватает, и строка описывается парой
`name` + `field`: `heart_rate/avg`, `sleep_analysis/deep`, `step_count/qty`.
Это обычный «длинный» формат временных рядов — ровно то, что Grafana ждёт от
запроса (время, значение, серия), и он принимает любое новое поле без миграции.

Числовые поля берутся ОТ ПРОТИВНОГО: всё, что не в `_SERVICE`, но приводится
к числу. Перечислять их поимённо значило бы молча терять метрики, которых
в Apple Health под сотню, а появляются они по мере того, как человек включает
их в выгрузке.

Модуль чистый: ни HTTP, ни ORM. Документы на входе, словари на выходе.
"""
from datetime import datetime, timezone

# Служебные ключи документа: это не измерения.
#
# `sleepStart`/`inBedEnd` — моменты, а не числа: их место было бы в отдельных
# колонках, а в «значение метрики» они не ложатся. Длительности сна (deep, rem,
# core, awake) при этом сохраняются, и график сна из них строится.
_SERVICE = frozenset({
    "_id", "__v", "date", "source", "units",
    "sleepStart", "sleepEnd", "inBedStart", "inBedEnd",
})


def _snake(key: str) -> str:
    """`Avg` → `avg`, `inBed` → `in_bed`. Имена полей приводим к одному виду."""
    out = []
    for i, ch in enumerate(key):
        if ch.isupper() and i and not key[i - 1].isupper():
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


def to_number(raw) -> float | None:
    """
    Значение метрики числом, или None — если измерения не было.

    Пустая строка означает «за этот день замера нет», и это НЕ ноль: пульс покоя,
    которого не сняли, — не пульс 0. Такие строки пропускаем, иначе они утянули бы
    вниз любое среднее.

    Запятая как десятичный разделитель — из сырых пакетов Health Auto Export
    (`"active_energy": "55,616"`, русская локаль телефона). Через API hae-server
    числа приходят уже разобранными, но разбор здесь стоит три строки и снимает
    зависимость от того, кто нас кормит.
    """
    if isinstance(raw, bool):  # bool — подкласс int, а метрикой быть не может
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        text = raw.strip().replace(",", ".")
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def to_moment(raw) -> datetime | None:
    """
    Момент замера в naive-UTC — той же конвенции, что и все даты проекта.

    Приходит в двух видах: `2026-06-19T00:00:00.000Z` из API и
    `2026-08-09 23:39:00 +0700` в сырых пакетах. Второй важен не сам по себе,
    а тем, что несёт смещение: без приведения к UTC ночной замер уехал бы
    на соседние сутки и сломал бы сопоставление с тренировкой.
    """
    if isinstance(raw, datetime):
        moment = raw
    elif isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            # `+0700` без двоеточия и прочие вольности телефона.
            try:
                moment = datetime.strptime(text, "%Y-%m-%d %H:%M:%S %z")
            except ValueError:
                return None
    else:
        return None

    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc).replace(tzinfo=None)
    return moment


def rows_from(name: str, docs) -> list[dict]:
    """
    Документы одной метрики → строки для health_metric.

    Один документ даёт СТОЛЬКО строк, сколько в нём числовых полей: у пульса это
    три (min/avg/max), у сна четыре, у шагов одна.
    """
    rows = []

    for doc in docs or ():
        if not isinstance(doc, dict):
            continue

        moment = to_moment(doc.get("date"))
        if moment is None:
            # Без момента строку некуда положить на ось времени, и ключ
            # уникальности неполон — молча пропускаем.
            continue

        units = doc.get("units") or None
        # Пустая строка, а не NULL: источник входит в ключ уникальности, а NULL
        # в нём не склеивается — две присылки без устройства дали бы две строки
        # вместо одной, и идемпотентность отвалилась бы именно там, где её
        # некому заметить.
        source = doc.get("source") or ""

        for key, raw in doc.items():
            if key in _SERVICE:
                continue
            value = to_number(raw)
            if value is None:
                continue
            rows.append({
                "name": name,
                "field": _snake(key),
                "measured_at": moment,
                "value": value,
                "units": units,
                "source": source,
            })

    return rows
