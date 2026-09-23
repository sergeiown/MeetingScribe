"""Minimal i18n: English source strings are the identity default (no en
table needed); other languages are a dict keyed by the exact English string.

Usage: tr("Some label") or tr("Deleted {n} files", n=3) for templates.
"""

from PySide6.QtCore import QSettings

_LANG_KEY = "language"
DEFAULT_LANGUAGE = "en"

_UK = {
    # main_window.py
    "Settings": "Налаштування",
    "Help": "Довідка",
    "About": "Про програму",
    "Files": "Файли",
    "Transcript": "Розшифровка",
    "File": "Файл",
    "Size": "Розмір",
    "Duration": "Тривалість",
    "Status": "Статус",
    "No files yet - click \"Add files...\" or drop some into input\\":
        "Ще немає файлів - натисніть \"Додати файли...\" або киньте їх у теку input\\",
    "The transcript will appear here once processing starts...":
        "Розшифровка з'явиться тут після початку обробки...",
    "Add files...": "Додати файли...",
    "Rename": "Перейменувати",
    "Rename file": "Перейменування файлу",
    "New name:": "Нова назва:",
    "Delete selected": "Видалити вибрані",
    "Options": "Опції",
    "Model:": "Модель:",
    "Language:": "Мова:",
    "Ukrainian": "Українська",
    "Auto-detect": "Автовизначення",
    "Speaker diarization": "Розділення за спікерами",
    "Automatically identify speakers\nafter recognition": "Автоматично визначати спікерів\nпісля розпізнавання",
    "Count (0 = auto):": "Кількість (0 = авто):",
    "Recognize speech": "Розпізнати мовлення",
    "Identify speakers": "Визначити спікерів",
    "Select a recognized file that hasn't had speakers identified yet.":
        "Виберіть розпізнаний файл, для якого ще не визначено спікерів.",
    "Cancel": "Скасувати",
    "Ready.": "Готово до роботи.",
    "Overall": "Загальний",
    "Current file": "Поточний файл",
    "Open output folder": "Відкрити теку результатів",
    "Exit": "Вихід",
    "Add audio/video files": "Додати аудіо/відео файли",
    "Media files": "Медіафайли",
    "About MeetingScribe": "Про MeetingScribe",
    "<b>MeetingScribe</b> v{version}<br><br>"
    "Record meetings, then transcribe with speaker diarization and "
    "known-speaker recognition. Everything runs on this machine - "
    "no audio ever leaves the host.<br><br>"
    "MIT License © Serhii Myshko<br>"
    '<a href="https://github.com/sergeiown/MeetingScribe">github.com/sergeiown/MeetingScribe</a>':
        "<b>MeetingScribe</b> v{version}<br><br>"
        "Записуй наради, потім розшифровуй їх з розділенням за спікерами та "
        "розпізнаванням відомих голосів. Все виконується на цьому "
        "комп'ютері - аудіо ніколи не залишає його.<br><br>"
        "Ліцензія MIT © Сергій Мишко<br>"
        '<a href="https://github.com/sergeiown/MeetingScribe">github.com/sergeiown/MeetingScribe</a>',
    "How to use": "Як користуватися",
    "How to use MeetingScribe": "Як користуватися MeetingScribe",
    "<b>Recording</b>"
    "<ol>"
    "<li>In the recording panel on the left, check <b>Microphone</b> and/or <b>System audio</b>, "
    "then click <b>Record</b>. Use <b>Pause</b>/<b>Resume</b> as needed, and <b>Stop</b> to finish - "
    "the new file appears in the list on the right automatically.</li>"
    "<li>Both sources always record in shared mode, so other apps can keep using the same "
    "microphone/output device at the same time.</li>"
    "</ol>"
    "<b>Playback</b>"
    "<ol>"
    "<li>Select a file and click <b>Play</b>, or double-click it in the list, to listen to it.</li>"
    "</ol>"
    "<b>Transcribing</b>"
    "<ol>"
    "<li>Add audio/video files with <b>Add files...</b> (or drop them into the <code>input</code> folder) - "
    "or just record them, as above.</li>"
    "<li>Select one or more files, pick a recognition model and language.</li>"
    "<li>Click <b>Recognize speech</b>. With automatic speaker identification off, it stops after "
    "a plain transcript - click <b>Identify speakers</b> whenever you're ready to add speaker labels.</li>"
    "<li>For unrecognized speakers, keep the suggested name or type your own, and optionally save "
    "their voiceprint for next time.</li>"
    "<li>Find the transcript in the <code>output</code> folder (one .txt per input file).</li>"
    "</ol>"
    "Installed models, saved speakers, your Hugging Face token, theme, recording devices, and "
    "interface language are all managed from <b>Settings</b>.":
        "<b>Запис</b>"
        "<ol>"
        "<li>У панелі запису зліва постав галочку на <b>Мікрофон</b> і/або <b>Системний звук</b>, "
        "потім натисни <b>Record</b>. Користуйся <b>Pause</b>/<b>Resume</b> за потреби, і <b>Stop</b>, "
        "щоб завершити - новий файл автоматично з'явиться у списку справа.</li>"
        "<li>Обидва джерела завжди записуються у спільному режимі, тож інші програми можуть "
        "користуватися тим самим мікрофоном/пристроєм виводу одночасно.</li>"
        "</ol>"
        "<b>Відтворення</b>"
        "<ol>"
        "<li>Вибери файл і натисни <b>Play</b>, або двічі клацни по ньому в списку, щоб прослухати.</li>"
        "</ol>"
        "<b>Транскрипція</b>"
        "<ol>"
        "<li>Додай аудіо/відео файли кнопкою <b>Додати файли...</b> (або поклади їх у папку <code>input</code>) - "
        "або просто запиши їх, як описано вище.</li>"
        "<li>Вибери один або кілька файлів, обери модель розпізнавання й мову.</li>"
        "<li>Натисни <b>Розпізнати мовлення</b>. Якщо автоматичне визначення спікерів вимкнено, процес "
        "зупиниться після звичайного транскрипту - натисни <b>Визначити спікерів</b>, коли будеш готовий "
        "додати мітки спікерів.</li>"
        "<li>Для нерозпізнаних спікерів залиш запропоноване ім'я або введи своє, і за бажанням збережи "
        "голосовий відбиток на майбутнє.</li>"
        "<li>Транскрипт з'явиться в папці <code>output</code> (один .txt на кожен вхідний файл).</li>"
        "</ol>"
        "Встановлені моделі, збережені спікери, токен Hugging Face, тема, пристрої запису та мова "
        "інтерфейсу керуються з <b>Settings</b>.",
    "Check for updates": "Перевірити оновлення",
    "Could not check for updates: {error}": "Не вдалося перевірити оновлення: {error}",
    "Already checking - hang on a moment.": "Перевірка вже триває - зачекай трохи.",
    "Update available": "Доступне оновлення",
    "MeetingScribe {version} is available (you have v{current}).":
        "Доступна MeetingScribe {version} (у вас v{current}).",
    "Open releases page": "Відкрити сторінку релізів",
    "You're using the latest version.": "У вас найновіша версія.",
    "Download && Install": "Завантажити і встановити",
    "Later": "Пізніше",
    "Downloading update": "Завантаження оновлення",
    "Downloading MeetingScribe {version}...": "Завантаження MeetingScribe {version}...",
    "Cancelling...": "Скасування...",
    "Could not download the update: {error}": "Не вдалося завантажити оновлення: {error}",
    "Update": "Оновлення",
    "A transcription is still running. Finish or cancel it, then try installing the update again.":
        "Розшифровка ще триває. Завершіть або скасуйте її, потім спробуйте встановити оновлення знову.",
    "Install update": "Встановлення оновлення",
    "Install version {version} now? MeetingScribe will close and reopen.\n\n"
    "Windows may show a security prompt for the installer since it isn't "
    "code-signed - that's expected.":
        "Встановити версію {version} зараз? MeetingScribe закриється і перезапуститься.\n\n"
        "Windows може показати запит безпеки для інсталятора, оскільки він не "
        "підписаний цифровим підписом - це очікувано.",
    "Update failed": "Помилка оновлення",
    "Could not start the installer: {error}": "Не вдалося запустити інсталятор: {error}",
    "MeetingScribe is already running.": "MeetingScribe вже запущено.",
    "Delete files": "Видалення файлів",
    "Select one or more files in the table first.": "Спочатку виберіть один або кілька файлів у таблиці.",
    "No files selected": "Файли не вибрані",
    "Delete {n} file(s) from input\\? This cannot be undone.\n\n{names}":
        "Видалити {n} файл(и) з input\\? Цю дію не можна скасувати.\n\n{names}",
    "No model": "Немає моделі",
    "Install a recognition model first (Settings > Models).":
        "Спочатку встановіть модель розпізнавання (Налаштування > Моделі).",
    "Already transcribed": "Вже розшифровано",
    "{n} file(s) already have output. Re-transcribe and overwrite them?\n\n{names}":
        "{n} файл(и) вже мають результат. Розшифрувати повторно та перезаписати?\n\n{names}",
    "Cancelling...": "Скасування...",
    "Done.": "Готово.",
    "Cancelled.": "Скасовано.",
    "Transcription running": "Триває розшифровка",
    "A transcription is still running. Cancel it and quit?":
        "Розшифровка ще триває. Скасувати її та вийти?",
    "No model installed - open Settings": "Немає встановленої моделі - відкрийте Налаштування",
    "  [recommended]": "  [рекомендовано]",
    "unknown": "невідомо",
    "✓ Transcribed": "✓ Розшифровано",
    "✓✓ Diarized": "✓✓ Розділено за спікерами",
    "○ Not recognized": "○ Не розпізнано",
    "Speaker diarization isn't available in this install.":
        "Розділення за спікерами недоступне в цьому встановленні.",
    "Enter a Hugging Face token and download the diarization "
    "models in Settings > Models.":
        "Введіть токен Hugging Face і завантажте моделі діаризації "
        "у Налаштування > Моделі.",
    "Enter a Hugging Face token in Settings > Models to enable this.":
        "Введіть токен Hugging Face у Налаштування > Моделі, щоб увімкнути це.",
    "Diarization models aren't downloaded yet - get them from Settings > Models.":
        "Моделі діаризації ще не завантажені - отримайте їх у Налаштування > Моделі.",
    "File %v of %m": "Файл %v з %m",
    "ETA {time}": "Залишилось {time}",
    "Saved: {path}": "Збережено: {path}",

    # widgets.py (ModelRowWidget)
    "Installed": "Встановлено",
    "Not installed": "Не встановлено",
    "Download": "Завантажити",
    "Needs HF_TOKEN - enter it above": "Потрібен HF_TOKEN - введіть його вище",
    "Failed: {error}": "Помилка: {error}",
    "Failed": "Помилка",

    # settings_dialog.py
    "Recognition (Whisper)": "Розпізнавання (Whisper)",
    "Diarization (pyannote)": "Діаризація (pyannote)",
    "Gated models - accept the license while logged in to Hugging Face:<br>"
    '<a href="https://hf.co/pyannote/speaker-diarization-3.1">speaker-diarization-3.1</a>, '
    '<a href="https://hf.co/pyannote/segmentation-3.0">segmentation-3.0</a>, '
    '<a href="https://hf.co/pyannote/embedding">embedding</a>':
        "Моделі з обмеженим доступом - прийміть ліцензію, увійшовши в Hugging Face:<br>"
        '<a href="https://hf.co/pyannote/speaker-diarization-3.1">speaker-diarization-3.1</a>, '
        '<a href="https://hf.co/pyannote/segmentation-3.0">segmentation-3.0</a>, '
        '<a href="https://hf.co/pyannote/embedding">embedding</a>',
    "Speaker": "Спікер",
    "Version": "Версія",
    "Date": "Дата",
    "Rename": "Перейменувати",
    "Delete version": "Видалити версію",
    "Delete all versions": "Видалити всі версії",
    "Export selected...": "Експортувати вибрані...",
    "Export all...": "Експортувати всі...",
    "Import...": "Імпортувати...",
    "Export selected": "Експорт вибраних",
    "Select one or more rows to export.": "Виберіть один або кілька рядків для експорту.",
    "Export all": "Експорт усіх",
    "There are no enrolled speakers to export.": "Немає жодного зареєстрованого спікера для експорту.",
    "Export speakers": "Експорт спікерів",
    "Exported {n} voiceprint(s) to {path}.": "Експортовано {n} голосовий(і) відбиток(и) у {path}.",
    "Export failed": "Помилка експорту",
    "Could not write the export file:\n\n{error}": "Не вдалося записати файл експорту:\n\n{error}",
    "Import speakers": "Імпорт спікерів",
    "Imported {n} voiceprint(s).": "Імпортовано {n} голосовий(і) відбиток(и).",
    "Import failed": "Помилка імпорту",
    "Could not read the import file:\n\n{error}": "Не вдалося прочитати файл імпорту:\n\n{error}",
    "Select a speaker to rename.": "Виберіть спікера для перейменування.",
    "Rename speaker": "Перейменування спікера",
    "New name:": "Нове ім'я:",
    "Select a version to delete.": "Виберіть версію для видалення.",
    "Delete {name}?": "Видалити {name}?",
    "Select a speaker to delete.": "Виберіть спікера для видалення.",
    'Delete ALL versions of "{name}"? This cannot be undone.':
        'Видалити УСІ версії "{name}"? Цю дію не можна скасувати.',
    'Hugging Face token: <a href="https://huggingface.co/settings/tokens">get one here</a>':
        'Токен Hugging Face: <a href="https://huggingface.co/settings/tokens">отримати тут</a>',
    "Show": "Показати",
    "Hide": "Приховати",
    "Save token": "Зберегти токен",
    "Theme:": "Тема:",
    "System (auto)": "Системна (авто)",
    "Light": "Світла",
    "Dark": "Темна",
    "Saved": "Збережено",
    "HF_TOKEN saved to config.env.": "HF_TOKEN збережено у config.env.",
    "Interface language:": "Мова інтерфейсу:",
    "Interface": "Інтерфейс",
    "Show a splash screen on startup": "Показувати заставку при запуску",
    "Prevent the system from sleeping while running": "Не давати системі переходити в сон під час роботи",
    "Recording": "Запис",
    "Microphone device:": "Пристрій мікрофона:",
    "System audio device:": "Пристрій системного звуку:",
    "System default": "За замовчуванням у системі",
    "Start/stop recording shortcut:": "Сполучення клавіш для запису/зупинки:",
    "Works system-wide, even while another app (like Teams) has focus - the same "
    "combination both starts and stops a recording.":
        "Працює по всій системі, навіть коли активна інша програма (наприклад, Teams) - те саме "
        "сполучення і починає, і зупиняє запис.",
    "Not set - click to set": "Не встановлено - натисни, щоб встановити",
    "Set shortcut": "Встановити сполучення",
    "Press the key combination you want, then click OK.":
        "Натисни потрібне сполучення клавіш, тоді натисни OK.",
    "OK": "OK",
    "Clear": "Очистити",
    "Hardware": "Обладнання",
    "CPU: {name}": "CPU: {name}",
    "GPU: {name}": "GPU: {name}",
    "GPU: {name} (not supported for acceleration)": "GPU: {name} (не підтримується для прискорення)",
    "Use for processing:": "Використовувати для обробки:",
    "Automatic (use GPU - recommended)": "Автоматично (використовувати GPU - рекомендовано)",
    "CPU only": "Лише CPU",
    "The GPU is much faster for speech recognition and speaker "
    "identification, especially with larger models. CPU works "
    "everywhere and leaves the GPU free for other tasks (e.g. "
    "gaming) while processing.":
        "GPU значно швидший для розпізнавання мовлення та визначення "
        "спікерів, особливо з важчими моделями. CPU працює завжди і "
        "залишає GPU вільним для інших задач (наприклад, ігор) під час "
        "обробки.",
    "This GPU isn't supported for acceleration here (NVIDIA/CUDA "
    "only), so everything runs on the CPU instead. It still "
    "works fine, just slower - especially with larger models.":
        "Ця GPU тут не підтримується для прискорення (лише NVIDIA/CUDA), "
        "тож усе працює на CPU. Це нормально, просто повільніше - "
        "особливо з важчими моделями.",
    "No GPU detected, so everything runs on the CPU. It works "
    "fine, just slower than a GPU would be, especially with "
    "larger models - an NVIDIA GPU would speed this up automatically.":
        "GPU не знайдено, тож усе працює на CPU. Це нормально, просто "
        "повільніше, ніж було б з GPU, особливо з важчими моделями - "
        "відеокарта NVIDIA автоматично пришвидшила б обробку.",
    "Models": "Моделі",
    "Speakers": "Спікери",
    "General": "Загальні",
    "Close": "Закрити",
    "Save and Close": "Зберегти і закрити",

    # dialogs.py
    "Unidentified speaker": "Невизначений спікер",
    "(no text samples)": "(немає текстових зразків)",
    "Name (leave empty to skip):": "Ім'я (залиште порожнім, щоб пропустити):",
    '"{name}" already in database ({file}).': '"{name}" вже є в базі ({file}).',
    "Overwrite": "Перезаписати",
    "Add new version": "Додати нову версію",
    "Save": "Зберегти",
    "Skip": "Пропустити",
    # app.py
    "Download failed": "Помилка завантаження",
    "Could not download the model: {error}\n\nYou can retry later from Settings > Models.":
        "Не вдалося завантажити модель: {error}\n\nМожна повторити пізніше через Налаштування > Моделі.",

    # bootstrap.py
    "MeetingScribe - Setting up": "MeetingScribe - Встановлення компонентів",
    "Starting...": "Починаємо...",
    "{time} elapsed": "минуло {time}",
    "MeetingScribe needs about 2-3 GB of speech-recognition "
    "components it doesn't bundle by default. This is a one-time "
    "download - how long it takes depends mostly on your internet "
    "speed, often several minutes. You won't need to do this again. "
    "The line below shows the exact package and size currently "
    "downloading.":
        "MeetingScribe потребує близько 2-3 ГБ компонентів розпізнавання "
        "мовлення, які не входять до типової поставки. Це одноразове "
        "завантаження - тривалість залежить переважно від швидкості вашого "
        "інтернету, часто кілька хвилин. Повторно це не знадобиться. "
        "Рядок нижче показує, який саме пакет і якого розміру завантажується "
        "зараз.",
    "Setup failed": "Помилка встановлення",
    "Could not install required components:\n\n{error}\n\n"
    "Try restarting the app, or install manually:\npip install -r \"{path}\"":
        "Не вдалося встановити необхідні компоненти:\n\n{error}\n\n"
        "Спробуйте перезапустити застосунок, або встановіть вручну:\npip install -r \"{path}\"",

    # core status messages (translated at the GUI display boundary)
    "Converting to WAV...": "Конвертація у WAV...",
    "Transcribing...": "Розшифровка...",
    "Loading diarization pipeline...": "Завантаження конвеєра діаризації...",
    "Diarizing...": "Діаризація...",
    "Diarization done.": "Діаризацію завершено.",
    "Speaker identification...": "Розпізнавання спікерів...",
    "Loading embedding model...": "Завантаження моделі ембеддингів...",
    "Embedding model not cached locally - attempting download...":
        "Модель ембеддингів не кешована локально - спроба завантаження...",

    # recording panel + playback (main_window.py)
    "Record audio": "Запис аудіо",
    "Microphone": "Мікрофон",
    "System audio": "Системний звук",
    "No microphone found": "Мікрофон не знайдено",
    "No output device found": "Пристрій виводу не знайдено",
    "Peak: {db} dB": "Пік: {db} дБ",
    "Record": "Запис",
    "Recording failed": "Помилка запису",
    "No microphone available.": "Немає доступного мікрофона.",
    "No system-audio output device available.": "Немає доступного пристрою для запису системного звуку.",
    "Recording": "Запис",
    "Recording in progress": "Триває запис",
    "A recording is still running. Stop it and quit?":
        "Запис ще триває. Зупинити його і вийти?",
    "Some files could not be deleted:\n\n{errors}":
        "Деякі файли не вдалося видалити:\n\n{errors}",
    "Play": "Відтворити",
    "Pause": "Пауза",
    "Resume": "Продовжити",
    "Stop": "Стоп",
}

_CATALOG = {"uk": _UK}


def available_languages():
    """[(code, native display name), ...] - names shown in their own
    language, never translated (same convention as the README's language
    switcher badges)."""
    return [("en", "English"), ("uk", "Українська")]


def get_language() -> str:
    return QSettings("MeetingScribe", "MeetingScribe").value(_LANG_KEY, DEFAULT_LANGUAGE)


def set_language(code: str) -> None:
    QSettings("MeetingScribe", "MeetingScribe").setValue(_LANG_KEY, code)


def tr(text: str, **kwargs) -> str:
    table = _CATALOG.get(get_language())
    translated = table.get(text, text) if table else text
    return translated.format(**kwargs) if kwargs else translated
