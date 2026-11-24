"""Russian strings of the 0.6.0 Studio page and the extended background card (merged into i18n_ru.RU)."""
from __future__ import annotations

RU_060: dict[str, str] = {
    # page and tabs
    "Studio": "Студия",
    "Professional tools: extra cameras and layouts, AI vision, privacy shield, colour, automation, audio, remote "
    "control and your own AI models. Everything runs on this PC.":
        "Профессиональные инструменты: дополнительные камеры и раскладки, ИИ-зрение, защита приватности, цвет, "
        "автоматизация, звук, удалённое управление и свои ИИ-модели. Всё работает на этом ПК.",
    "Multi-Camera": "Мультикамера", "AI Vision": "ИИ-зрение", "Privacy Shield": "Защита приватности",
    "Color Studio": "Цвет", "Automation": "Автоматизация", "Audio Studio": "Звук", "Remote Control": "Удалённое управление",
    "AI Models": "ИИ-модели",
    "—": "—", "°": "°", "×": "×", "yes": "да", "no": "нет", "any": "любая", "None": "Нет", "Reset": "Сбросить",
    "Show": "Показать", "Hide": "Скрыть", "Port": "Порт", "Check": "Проверить", "Added.": "Добавлено.",
    "Downloading…": "Загрузка…", "Checking…": "Проверка…", "Choose…": "Выбрать…", "Name": "Имя", "Remove": "Удалить",
    "Enable / disable": "Включить / выключить", "off": "выкл.", "missing": "нет файла", "stopped": "остановлено",
    # AI models
    "AI backends on this PC": "ИИ-бэкенды этого ПК", "Check a model": "Проверить модель", "My models": "Мои модели",
    "Runs a small model on every execution provider and shows which one really ran it. A provider can be listed and "
    "still fall back to the processor.":
        "Запускает маленькую модель на каждом провайдере и показывает, какой из них действительно её выполнил. "
        "Провайдер может быть в списке и всё равно переключаться на процессор.",
    "Check backends": "Проверить бэкенды",
    "Checks the model's inputs and outputs against the category, runs it on a sample, measures it on each backend "
    "and records its SHA-256. Nothing in the file is run as code.":
        "Сверяет входы и выходы модели с категорией, запускает её на образце, замеряет на каждом бэкенде и "
        "записывает SHA-256. Ничего из файла не выполняется как код.",
    "Model file (.onnx)": "Файл модели (.onnx)", "Add to my models": "Добавить в мои модели",
    "Choose a model": "Выберите модель", "Fits the category.": "Подходит под категорию.",
    "Does not fit: {why}": "Не подходит: {why}", "Inputs: {v}": "Входы: {v}",
    "Outputs (sample run): {v}": "Выходы (пробный запуск): {v}", "Backends: {v}": "Бэкенды: {v}",
    "Choose a model file first.": "Сначала выберите файл модели.", "Licence: {v}": "Лицензия: {v}",
    "Used by: {v}": "Используется: {v}",
    "No module of the app decodes this layout yet: the model is kept and measured only.":
        "Пока ни один модуль приложения не разбирает такой формат выходов: модель хранится и замерена, но не "
        "используется.",
    "Check failed: {e}": "Проверка не удалась: {e}", "{p}: works · {ms} ms": "{p}: работает · {ms} мс",
    "kept only": "только хранится",
    "Use in the app": "Использовать в приложении", "Back to the built-in model": "Вернуть встроенную модель",
    "in use": "используется", "{name} is used for {role}.": "{name} используется для: {role}.",
    "The built-in model is used for {role}.": "Для «{role}» используется встроенная модель.",
    "background": "фон", "text": "текст", "pose": "поза", "hands": "руки",
    "face": "лица", "mesh": "сетка лица", "Cannot check: {e}": "Не удаётся проверить: {e}",
    "{p}: listed, but the model ran on {used}": "{p}: есть в списке, но модель выполнилась на {used}",
    "{p}: does not work ({e})": "{p}: не работает ({e})", "fails": "ошибка", "file missing": "нет файла",
    "Category": "Категория", "Fastest backend": "Самый быстрый бэкенд", "Used by": "Используется",
    "Enabled": "Включена",
    "Object detection": "Обнаружение объектов", "Instance segmentation": "Сегментация экземпляров",
    "Semantic segmentation": "Семантическая сегментация", "Background segmentation": "Сегментация фона",
    "Face detection": "Обнаружение лиц", "Face landmarks": "Точки лица", "Body pose": "Поза тела",
    "Hand landmarks": "Точки рук", "OCR (text detection / recognition)": "OCR (поиск / распознавание текста)",
    # multi-camera
    "Single camera": "Одна камера", "Picture-in-picture": "Картинка в картинке", "Side by side": "Рядом",
    "Split (top / bottom)": "Сверху и снизу", "2 × 2 grid": "Сетка 2 × 2", "Custom tiles": "Свои плитки",
    "Show “no signal”": "Показать «нет сигнала»", "Hold the last picture (3 s)": "Держать последний кадр (3 с)",
    "Leave it out": "Не показывать", "Layout of this scene": "Раскладка этой сцены", "Extra sources": "Доп. источники",
    "Extra sources are drawn on the finished picture. The main input (Input page) keeps pacing the camera: a slow or "
    "lost extra source never slows it down.":
        "Дополнительные источники рисуются на готовой картинке. Темп камеры задаёт основной вход (страница «Вход»): "
        "медленный или пропавший доп. источник её не замедляет.",
    "Use extra cameras in this scene": "Использовать доп. камеры в этой сцене", "Layout": "Раскладка",
    "Main picture": "Основная картинка", "Window corner": "Угол окна", "Window size": "Размер окна",
    "Lost source": "Пропавший источник", "Automatic switching": "Автопереключение", "Switch to": "Переключать на",
    "Bottom right": "Справа снизу", "Bottom left": "Слева снизу", "Top right": "Справа сверху", "Top left": "Слева сверху",
    "Off": "Выкл.", "Switch when nobody is on the main camera": "Переключать, когда в основной камере никого нет",
    "Camera": "Камера", "Screen or window": "Экран или окно", "Video file": "Видеофайл", "Test pattern": "Тестовый шаблон",
    "Add source": "Добавить источник", "Remove source": "Убрать источник", "Next camera": "Следующая камера",
    "Next layout": "Следующая раскладка", "Choose a video": "Выберите видео",
    "Videos (*.mp4 *.mov *.mkv *.avi)": "Видео (*.mp4 *.mov *.mkv *.avi)",
    "Showing: {p} · composition {ms} ms · stale tiles {n} · auto switches {a}":
        "Показано: {p} · сборка {ms} мс · устаревших плиток {n} · автопереключений {a}",
    "Up to {n} besides the main input. This app's own camera cannot be used (it would show itself).":
        "До {n} помимо основного входа. Камеру самого приложения использовать нельзя (она показала бы саму себя).",
    "At most {n} extra sources.": "Не больше {n} доп. источников.", "Cannot list cameras: {e}": "Не удаётся получить список камер: {e}",
    "Main input": "Основной вход", "Choose a video file first.": "Сначала выберите видеофайл.",
    "Id": "Id", "Source": "Источник", "State": "Состояние", "FPS": "FPS", "Size": "Размер", "Age": "Возраст",
    # vision
    "Face points and head direction, body pose and hand skeletons, analysed on the camera picture a few times per "
    "second. It is the first thing slowed down when the PC is busy; the camera keeps its frame rate. Nothing here "
    "recognises who a person is.":
        "Точки лица и направление головы, поза тела и скелет рук — анализ картинки камеры несколько раз в секунду. "
        "При нагрузке это замедляется первым, частота кадров камеры сохраняется. Ничто здесь не определяет, кто "
        "этот человек.",
    "Analyse the picture": "Анализировать картинку", "Face points and head direction": "Точки лица и направление головы",
    "Body pose (skeleton)": "Поза тела (скелет)", "Hands (21 points per hand)": "Руки (21 точка на руку)",
    "Draw on the camera picture": "Рисовать на картинке камеры", "Analyses per second": "Анализов в секунду",
    "Models": "Модели", "Download body pose models (17 MB)": "Скачать модели позы тела (17 МБ)",
    "Face: built in (YuNet).": "Лицо: встроено (YuNet).", "Body pose: downloaded.": "Поза тела: скачана.",
    "Body pose: not downloaded.": "Поза тела: не скачана.", "Hands: downloaded.": "Руки: скачаны.",
    "Hands: not downloaded (Settings → Gestures → Download).": "Руки: не скачаны (Настройки → Жесты → Скачать).",
    "State: {st} · {fps} analyses/s · faces {f} · poses {p} · hands {h}":
        "Состояние: {st} · {fps} анализов/с · лиц {f} · поз {p} · рук {h}",
    "Models missing: {m}": "Нет моделей: {m}",
    "AI Vision (face, pose, hands)": "ИИ-зрение (лицо, поза, руки)",
    "People in view": "Люди в кадре", "Object": "Объект", "Confidence": "Уверенность", "Locked": "Закреплён",
    "Lock": "Закрепить", "Unlock": "Открепить",
    "Dense face mesh (478 points, up to 2 faces)": "Плотная сетка лица (478 точек, до 2 лиц)",
    "Face mesh: downloaded.": "Сетка лица: скачана.", "Face mesh: not downloaded.": "Сетка лица: не скачана.",
    "Download missing models ({mb} MB)": "Скачать недостающие модели ({mb} МБ)",
    "People (segmentation)": "Люди (сегментация)",
    "Distance (depth: nearer than the edge setting)": "Расстояние (глубина: ближе порога края)",
    "Separate by": "Отделять по", "Download the depth model (27 MB)": "Скачать модель глубины (27 МБ)",
    "Auto-framing follows only locked people": "Автокадрирование следит только за закреплёнными",
    "Tracked people (object detection must be on). A locked person keeps their number through short occlusions (up "
    "to 10 s) and is found again by position; auto-framing can follow only the locked people.":
        "Отслеживаемые люди (нужно включённое распознавание объектов). Закреплённый человек сохраняет номер при "
        "коротких перекрытиях (до 10 с) и находится снова по положению; автокадрирование может следить только за "
        "закреплёнными.", "Download failed: {e}": "Загрузка не удалась: {e}",
    "running": "работает", "no models": "нет моделей", "error": "ошибка", "holding": "удерживает",
    # privacy
    "E-mail addresses": "Адреса e-mail", "Phone numbers": "Номера телефонов", "Payment card numbers": "Номера карт",
    "Account-like numbers (IBAN, long numbers)": "Номера счетов (IBAN, длинные номера)", "Street addresses": "Адреса",
    "My keywords": "Мои слова", "Privacy mode (faces and screens)": "Режим приватности (лица и экраны)",
    "Privacy shield (text and areas)": "Защита приватности (текст и области)", "Areas": "Области",
    "Hides detected faces, then also monitors, laptops, phones and books, then also blurs the background (hotkey "
    "Ctrl+Alt+H).":
        "Скрывает найденные лица, затем также мониторы, ноутбуки, телефоны и книги, затем ещё и размывает фон "
        "(клавиши Ctrl+Alt+H).",
    "Faces": "Лица", "Faces and screens": "Лица и экраны", "Faces, screens and background": "Лица, экраны и фон",
    "Finds text in the picture on this PC and hides it, plus the areas you mark. It reduces what leaves the PC; it is "
    "not a guarantee: small (under about 12 px), slanted, handwritten or faint text is often missed, and e-mail "
    "addresses are recognised only by hiding all text. Extra cameras of a layout are not covered.":
        "Находит текст на картинке прямо на этом ПК и скрывает его, а также отмеченные вами области. Это уменьшает "
        "то, что уходит с ПК, но не гарантия: мелкий (меньше ~12 px), наклонный, рукописный или бледный текст "
        "часто пропускается, а адреса e-mail скрываются только в режиме «весь текст». Доп. камеры раскладки не "
        "защищаются.",
    "Use the privacy shield": "Включить защиту", "Text": "Текст", "Do not look for text": "Не искать текст",
    "Hide all text": "Скрывать весь текст", "Hide only sensitive text": "Скрывать только чувствительный текст",
    "Effect": "Эффект", "Pixelate": "Пикселизация", "Blur": "Размытие", "Black box": "Чёрный прямоугольник",
    "Padding (× text height)": "Отступ (× высота текста)", "Sensitivity": "Чувствительность",
    "Strict: keep areas hidden while detection is late or failing":
        "Строгий режим: держать области скрытыми, пока поиск запаздывает или сбоит",
    "Keywords, separated by commas": "Слова через запятую",
    "Always hidden areas and areas never hidden by text detection, in shares of the picture: x, y, width, height "
    "(0..1).":
        "Всегда скрытые области и области, которые поиск текста никогда не скрывает, в долях картинки: x, y, "
        "ширина, высота (0..1).",
    "Always hide": "Всегда скрывать", "Never hide": "Никогда не скрывать", "Remove area": "Убрать область",
    "Text detection: ready.": "Поиск текста: готов.", "Text detection model not downloaded.": "Модель поиска текста не скачана.",
    "“Only sensitive text” needs the text recognition model (not downloaded): until then all text is hidden.":
        "Для «только чувствительного текста» нужна модель распознавания текста (не скачана): пока скрывается весь "
        "текст.",
    "Enter four numbers: x, y, width, height (0..1).": "Введите четыре числа: x, y, ширина, высота (0..1).",
    "Shield: {st} · text boxes {b} · hidden now {h} · detection {ms} ms":
        "Защита: {st} · текстовых блоков {b} · скрыто сейчас {h} · поиск {ms} мс",
    "hide": "скрывать", "never hide": "не скрывать",
    "Hide faces by their outline (needs AI Vision; precise with the face mesh)":
        "Скрывать лица по контуру (нужно ИИ-зрение; точно — с сеткой лица)",
    "Download the text detection model (3 MB)": "Скачать модель поиска текста (3 МБ)",
    "Download the text recognition model (16 MB)": "Скачать модель распознавания текста (16 МБ)",
    # colour
    "Exposure": "Экспозиция", "Brightness": "Яркость", "Contrast": "Контраст", "Saturation": "Насыщенность",
    "Temperature": "Температура", "Tint": "Оттенок", "Gamma": "Гамма", "Highlights": "Света", "Shadows": "Тени",
    "Sharpness": "Резкость", "Vignette": "Виньетка", "Film grain": "Зерно", "Soft glow": "Мягкое свечение",
    "LUT intensity": "Сила LUT", "Colour of this scene": "Цвет этой сцены", "Presets": "Пресеты",
    "Applied once to the camera picture, after automatic lighting and before backgrounds and replacements. Runs on "
    "the processor: everything at once costs about 55 ms per 1080p frame on a 2-core PC; tone and saturation alone a "
    "few ms.":
        "Применяется один раз к картинке камеры: после автосвета, до фонов и замен. Работает на процессоре: всё "
        "сразу — около 55 мс на кадр 1080p на 2-ядерном ПК, только тон и насыщенность — несколько мс.",
    "Grade this scene": "Цветокоррекция этой сцены", "Protect skin tones from saturation changes":
        "Защищать тон кожи от изменения насыщенности",
    "Import .cube…": "Импорт .cube…", "Preset name": "Имя пресета", "Save as preset": "Сохранить как пресет",
    "Apply preset": "Применить пресет", "LUT added: {name}": "LUT добавлен: {name}", "LUT": "LUT",
    "Choose a LUT": "Выберите LUT", "Give the preset a name.": "Дайте пресету имя.",
    "Grading: {ms} ms per frame": "Цветокоррекция: {ms} мс на кадр",
    "reduced: sharpness, glow, grain and skin protection skipped to keep the frame rate":
        "упрощено: резкость, свечение, зерно и защита кожи пропущены, чтобы сохранить частоту кадров",
    "Cannot use this LUT: {e}": "Этот LUT не подходит: {e}",
    # automation
    "Person appears": "Появился человек", "Nobody in view": "Никого нет в кадре",
    "Number of people (e.g. >= 2)": "Число людей (например, >= 2)", "Object appears (class name)": "Появился объект (класс)",
    "Number of faces changes": "Изменилось число лиц",
    "Gesture (open_palm, victory, hand_raised, …)": "Жест (open_palm, victory, hand_raised, …)",
    "Camera lost (main or cam1…)": "Камера пропала (main или cam1…)", "Recording starts": "Запись началась",
    "Recording stops": "Запись остановлена", "Every N seconds": "Каждые N секунд",
    "Automation hotkey 1-4": "Клавиша автоматизации 1–4", "Only when run by hand": "Только при ручном запуске",
    "Switch to scene": "Переключить сцену", "App action (id)": "Действие приложения (id)", "Privacy level": "Уровень приватности",
    "Recording (start / stop)": "Запись (start / stop)", "Save instant replay": "Сохранить повтор",
    "Show camera": "Показать камеру", "Camera layout": "Раскладка камер", "Colour preset": "Цветовой пресет",
    "Effects (on / off)": "Эффекты (on / off)", "New rule": "Новое правило", "Rules": "Правила", "History": "История",
    "Rules switch scenes or run actions when something happens. A rule waits for its cooldown before it runs again; "
    "state triggers must hold for the debounce time; too many automatic scene switches in a row pause scene rules for "
    "30 s.":
        "Правила переключают сцены или выполняют действия, когда что-то происходит. Правило ждёт паузу перед "
        "повтором; условия состояния должны держаться время подтверждения; слишком частые автопереключения сцен "
        "приостанавливают правила сцен на 30 с.",
    "Run automation rules": "Выполнять правила", "When": "Когда", "Value": "Значение", "Only in scene": "Только в сцене",
    "Do": "Сделать", "Priority": "Приоритет", "Cooldown, s": "Пауза, с", "Debounce, s": "Подтверждение, с",
    "Add rule": "Добавить правило", "Run now": "Выполнить сейчас", "Rule added.": "Правило добавлено.",
    "Any scene": "Любая сцена", "No scene with that name.": "Нет сцены с таким именем.",
    "This rule is not complete.": "Правило заполнено не до конца.", "Turn automation on first.": "Сначала включите автоматизацию.",
    "Scene": "Сцена", "Cooldown": "Пауза", "Time": "Время", "Rule": "Правило", "Trigger": "Условие",
    "Action": "Действие", "Result": "Результат", "Scene name": "Имя сцены",
    # audio
    "Microphone": "Микрофон", "Processing": "Обработка",
    "Sound for recordings and instant replay. The virtual camera itself carries no sound: Windows cameras are video "
    "only, and a system-wide virtual microphone needs a signed audio driver (not part of this version).":
        "Звук для записей и мгновенного повтора. Сама виртуальная камера звук не передаёт: камеры Windows — только "
        "видео, а системному виртуальному микрофону нужен подписанный аудиодрайвер (в этой версии его нет).",
    "Refresh": "Обновить", "No sound": "Без звука", "Show live levels (uses the microphone while on)":
        "Показывать уровни (микрофон включён, пока отмечено)",
    "Hear the processed microphone (use headphones: speakers feed back)":
        "Слышать обработанный микрофон (в наушниках: через колонки будет обратная связь)",
    "monitoring: {e}": "прослушка: {e}",
    "Input": "Вход", "Output": "Выход", "microphone off": "микрофон выключен", "microphone on: {d}": "микрофон: {d}",
    "{state} · input RMS {i} dB · output RMS {o} dB · gate {g} · compressor {c} dB · limiter {lim} dB":
        "{state} · вход RMS {i} дБ · выход RMS {o} дБ · гейт {g} · компрессор {c} дБ · лимитер {lim} дБ",
    "Cannot list microphones: {e}": "Не удаётся получить список микрофонов: {e}", "not connected": "не подключён",
    "open": "открыт", "closed": "закрыт", "Mute": "Без звука", "Noise suppression": "Шумоподавление",
    "Noise gate": "Шумовой гейт", "Equaliser": "Эквалайзер", "Compressor": "Компрессор", "Automatic gain": "Автоусиление",
    "Limiter": "Лимитер", "Input gain, dB": "Усиление входа, дБ", "Noise suppression strength": "Сила шумоподавления",
    "Gate threshold, dBFS": "Порог гейта, dBFS", "Bass (120 Hz), dB": "Низы (120 Гц), дБ", "Mids (1 kHz), dB": "Середина (1 кГц), дБ",
    "Treble (6 kHz), dB": "Верха (6 кГц), дБ", "Compressor threshold, dBFS": "Порог компрессора, dBFS",
    "Compressor ratio": "Степень сжатия", "Make-up gain, dB": "Компенсация, дБ",
    "Automatic gain target, dBFS": "Цель автоусиления, dBFS", "Limiter ceiling, dBFS": "Потолок лимитера, dBFS",
    # remote
    "Remote control": "Удалённое управление", "Access": "Доступ",
    "A page for a phone or a second screen: scene buttons, recording, privacy, effects and camera switching. It runs "
    "inside this app; nothing goes through the internet. Every request needs the access code below.":
        "Страница для телефона или второго экрана: кнопки сцен, запись, приватность, эффекты и переключение камер. "
        "Работает внутри этого приложения, через интернет ничего не идёт. Каждый запрос требует кода доступа ниже.",
    "Allow remote control": "Разрешить удалённое управление",
    "Also allow other devices on my network (phone)": "Разрешить и другим устройствам в моей сети (телефон)",
    "Warning: anyone on this network who knows the access code can switch scenes, start recordings and turn privacy "
    "off. Use only on a network you trust; Windows Firewall may ask to allow the app.":
        "Внимание: любой в этой сети, кто знает код доступа, может переключать сцены, начинать запись и выключать "
        "приватность. Используйте только в доверенной сети; брандмауэр Windows может спросить разрешение.",
    "Copy code": "Копировать код", "New code": "Новый код",
    "“New code” disconnects every phone or page using the old one. The code is stored encrypted for your Windows "
    "account.":
        "«Новый код» отключает все телефоны и страницы со старым кодом. Код хранится зашифрованным для вашей учётной "
        "записи Windows.",
    "Requests: {n} · live pages: {w}": "Запросов: {n} · открытых страниц: {w}",
    # background card (replacement page)
    "Colour": "Цвет", "Gradient": "Градиент", "Looping video from the library": "Зацикленное видео из библиотеки",
    "Second colour": "Второй цвет", "Direction": "Направление", "Mask quality": "Качество маски",
    "Top to bottom": "Сверху вниз", "Left to right": "Слева направо", "Diagonal": "По диагонали", "Fast": "Быстро",
    "Balanced": "Сбалансированно", "Quality (edges follow the picture)": "Качество (края следуют картинке)",
    "(choose a video)": "(выберите видео)",
    "Choose a video: add one to the asset library above first.": "Выберите видео: сначала добавьте его в библиотеку выше.",
    # actions (hotkeys page)
    "Next camera layout": "Следующая раскладка камер",
    "Hide cards, documents and licence plates whole (outline with text inside)":
        "Скрывать карты, документы и номера целиком (контур с текстом внутри)",
    "document": "документ", "plate": "номер",
    "Speaker detection: models missing (AI Models)": "Определение говорящего: нет моделей (AI Models)",
    "Speaker detection: {state} · speaking: {who} · mouth movement: {act}":
        "Определение говорящего: {state} · говорит: {who} · движение губ: {act}",
    "Show who is speaking (mouth movement; needs the face models)":
        "Показывать говорящего (по движению губ; нужны модели лица)",
    "Gaze and eye contact": "Взгляд и зрительный контакт",
    "Where the eyes look, from the irises of the face mesh and the head direction. Eye contact correction moves the "
    "irises towards the camera inside the eye opening; it stays off while blinking, with the head turned or when the "
    "person clearly looks elsewhere. Needs the face mesh model.":
        "Куда смотрят глаза — по радужкам сетки лица и направлению головы. Коррекция зрительного контакта сдвигает "
        "радужки к камере внутри разреза глаза; она не работает при моргании, повёрнутой голове или когда человек "
        "явно смотрит в сторону. Нужна модель сетки лица.",
    "Gaze direction": "Направление взгляда",
    "Eye contact correction": "Коррекция зрительного контакта",
    "Correction strength, %": "Сила коррекции, %",
    "Calibrate: I am looking at the camera": "Калибровка: я смотрю в камеру",
    "Reset calibration": "Сбросить калибровку",
    "Calibrated: yaw {y}°, pitch {p}°": "Откалибровано: по горизонтали {y}°, по вертикали {p}°",
    "No confident gaze right now: turn gaze on and look at the camera.":
        "Сейчас взгляд не определён уверенно: включите взгляд и посмотрите в камеру.",
    "Calibration reset.": "Калибровка сброшена.",
    "Face {n}: {dir} · off camera {y}° / {p}° · confidence {c} · stability {s}":
        "Лицо {n}: {dir} · отклонение от камеры {y}° / {p}° · уверенность {c} · стабильность {s}",
    "No eyes found.": "Глаза не найдены.",
    "Eye contact: {n} eyes corrected · frames skipped (mesh not fresh) {k}":
        "Зрительный контакт: исправлено глаз {n} · пропущено кадров (сетка устарела) {k}",
    "at camera": "в камеру", "left": "влево", "right": "вправо", "up": "вверх", "down": "вниз",
    "unknown": "неизвестно",
    "Also check the extra cameras (Multi-Camera); strict: an unchecked camera is hidden whole":
        "Проверять и дополнительные камеры (Multi-Camera); в строгом режиме непроверенная камера скрывается целиком",
    "Extra cameras: {v}": "Доп. камеры: {v}",
    "hidden whole (not checked yet)": "скрыта целиком (ещё не проверена)",
    "{n} hidden": "скрыто {n}",
    "De-esser": "Де-эссер", "De-esser threshold, dBFS": "Порог де-эссера, dBFS", "De-esser from, Hz": "Де-эссер от, Гц",
    "Preset": "Пресет", "Custom": "Свой", "Clean (limiter only)": "Чистый (только лимитер)", "Voice": "Голос",
    "Podcast": "Подкаст", "Noisy room": "Шумная комната",
    "Looks at the camera (AI Vision gaze)": "Смотрит в камеру (взгляд, AI Vision)",
    "Looks away from the camera (AI Vision gaze)": "Отвёл взгляд от камеры (взгляд, AI Vision)",
    "Colour screen (chroma key: green / blue screen)": "Цветной фон (хромакей: зелёный / синий экран)",
    "Green screen": "Зелёный экран", "Blue screen": "Синий экран", "Pick a colour…": "Выбрать цвет…",
    "Screen colour": "Цвет фона", "Similarity": "Схожесть", "Edge softness": "Мягкость края",
    "Spill suppression": "Подавление засветки", "Feather, px": "Растушёвка, пикс",
    "Transition": "Переход", "Transition time, s": "Время перехода, с", "Cut": "Склейка",
    "Cross-fade": "Наплыв", "Slide": "Сдвиг",
    "And if": "И если", "Condition": "Условие", "Condition: {e}": "Условие: {e}",
    "Selected camera: colour preset": "Выбранная камера: цветовой пресет", "Privacy": "Приватность",
    "Privacy shield checks it": "Проверяет Privacy Shield", "Always hidden whole": "Всегда скрыта целиком",
    "Not checked": "Не проверяется",
    "Virtual studio lights": "Виртуальный студийный свет",
    "Key, fill and rim light painted onto the person, aimed at the largest face (needs face detection or AI Vision). "
    "With background segmentation on, only the person is lit, never the background. 2-D relighting: it brightens "
    "and tints, it does not cast new shadows.":
        "Рисующий, заполняющий и контровой свет на человеке, направленный на самое крупное лицо (нужно определение "
        "лиц или AI Vision). При включённой сегментации фона освещается только человек, не фон. Это 2D-перерисовка "
        "света: она осветляет и тонирует, но не создаёт новых теней.",
    "Use virtual lights in this scene": "Виртуальный свет в этой сцене", "Key light": "Рисующий свет",
    "Key direction, ° (− left, + right)": "Направление рисующего, ° (− слева, + справа)",
    "Key colour, K": "Цвет рисующего, K", "Fill light": "Заполняющий свет", "Rim light": "Контровой свет",
    "Rim colour, K": "Цвет контрового, K", "Softness": "Мягкость", "Soft": "Мягкий",
    "Dramatic": "Драматичный", "Warm": "Тёплый", "Cool": "Холодный", "Lights: on the face": "Свет: на лице",
    "Lights: waiting for a face (turn on face detection or AI Vision)":
        "Свет: ждёт лицо (включите определение лиц или AI Vision)",
    " s": " с", "Silhouette key frames every": "Ключевые кадры силуэтов каждые",
    "SAM 2.1 silhouettes are made this often; in between they follow the motion (optical flow). 0 = SAM on every "
    "detection (slow without a GPU).": "Силуэты SAM 2.1 строятся с этим интервалом, между ними они следуют за "
    "движением (оптический поток). 0 — SAM на каждой детекции (медленно без GPU).",
    "Animation (drawn rectangles)": "Анимация (нарисованные прямоугольники)", "Animate this region": "Анимировать область",
    "Keyframes below": "Ключевые кадры ниже", "Pulse (opacity)": "Пульсация (прозрачность)",
    "Slide in from the left": "Въезд слева", "Zoom": "Масштаб", "Spin": "Вращение", "Sway": "Покачивание",
    "Repeat": "Повтор", "Back and forth": "Туда и обратно", "Smooth": "Плавно", "Linear": "Линейно",
    "Add key: current geometry at this time": "Ключ: текущая геометрия в это время", "Clear keys": "Очистить ключи",
    "Duration": "Длительность", "Playback": "Воспроизведение", "Easing": "Сглаживание", "Key time": "Время ключа",
    "Key opacity": "Прозрачность ключа", "{n} keys": "ключей: {n}",
    "at least 2 keys are needed": "нужно не меньше 2 ключей",
    **{f"Automation trigger {n}": f"Триггер автоматизации {n}" for n in range(1, 5)},
}
