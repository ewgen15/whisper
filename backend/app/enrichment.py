import json
import os
from openai import OpenAI

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

_SYSTEM_PROMPT = """
Ти — асистент особистого щоденника. Отримуєш сирий транскрибований текст голосового запису.

Поверни JSON об'єкт з такими полями:
- title: стисла, змістовна назва українською (до 8 слів)
- text: легко відредагована версія транскрипції (виправ слова-паразити, незначні граматичні помилки, зберігай живий стиль мовлення)
- emotional_state: один із [Позитивний, Гарний, Нейтральний, Поганий, Пригнічений]
- mood: масив тегів настрою українською (напр. ["засмучення", "розчарування", "надія"])
- physical_state: один із [Гарний, Бадьорий, Нейтральний, Втомлений, Поганий, Хворий]
- context: короткий опис ситуації або контексту
- project: назва проекту або діяльності якщо згадується, інакше null
- tags: масив тематичних тегів українською
- entry_type: один із [Рефлексія, Спостереження, Планування, Ідея, Подія, Інсайт, Досвід, Аналіз, Проблема, Рішення, Роздуми, Оновлення, Не визначено]
- time_of_day: один із [Ранок, День, Вечір, Ніч]
- points: масив з 3-6 коротких тез (по одному реченню), що передають суть запису

Відповідай ТІЛЬКИ валідним JSON, без пояснень.
"""


def enrich(raw_text: str) -> dict:
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": raw_text},
        ],
        response_format={"type": "json_object"},
        temperature=0.3,
    )
    return json.loads(response.choices[0].message.content)
