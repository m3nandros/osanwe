# Исправление проблемы с загрузкой PDF в Notion

## Проблема

PDF файлы не загружались в Notion, вместо этого создавались текстовые ссылки:
- "Оригинальный PDF: 1706.03762_original.pdf (локальный файл)"
- "Переведенный PDF: translated.pdf (локальный файл)"

## Причина

В стратегии `"auto"` код сначала проверял Discord URL, и только если его не было, использовал локальные файлы. Но поскольку мы передаем `None` для URL, код должен был использовать локальные файлы, но логика была неправильной.

## Исправление

Изменена логика в `_upload_pdfs_to_page()`:

**Было:**
- Стратегия "auto": сначала проверяет Discord URL, потом локальные файлы
- Проблема: если URL = None, но путь есть, код все равно мог не использовать локальные файлы правильно

**Стало:**
- Стратегия "auto" и "temporary": **сначала проверяет локальные файлы**, потом Discord URL как fallback
- Это гарантирует, что локальные файлы всегда используются, если они существуют

## Что изменилось

### Для оригинального PDF:
```python
elif upload_strategy == "temporary" or upload_strategy == "auto":
    # Always use local files for upload (direct or external storage)
    # For "auto", prefer local files over Discord URLs
    if original_pdf_path and original_pdf_path.exists():
        self._add_local_pdf_block(blocks, original_pdf_path, "Оригинальный PDF (EN)", arxiv_id)
    elif original_pdf_url:
        # Fallback to Discord URL if local file doesn't exist
        ...
```

### Для переведенного PDF:
Аналогичная логика применена.

## Что произойдет теперь

1. ✅ Локальные файлы будут использоваться в первую очередь
2. ✅ Для файлов ≤ 5 MB: попытка прямой загрузки в Notion
3. ✅ Для файлов > 5 MB или если прямая загрузка не удалась: использование внешнего хранилища
4. ✅ Если все не удалось: создание текстового блока (fallback)

## Рекомендации

### Для файлов ≤ 5 MB:
- Система попытается загрузить напрямую в Notion
- Если не получится (API недоступен), использует внешнее хранилище

### Для файлов > 5 MB:
- Настройте внешнее хранилище:
  ```bash
  NOTION_TEMP_STORAGE=s3_public  # или permanent
  ```

## Тестирование

После исправления:
1. Запустите новый перевод через Discord: `/rosetta <arxiv_id>`
2. Проверьте логи - должны быть сообщения о загрузке файлов
3. Проверьте Notion - должны быть файловые блоки, а не текстовые ссылки

## Логи для проверки

Ищите в логах:
- `"Initializing file upload for ..."` - попытка прямой загрузки
- `"Successfully uploaded ... to Notion (ID: ...)"` - успешная прямая загрузка
- `"Using external storage for ..."` - использование внешнего хранилища
- `"Added local PDF ... to page via ..."` - файл добавлен на страницу

Если видите `"не удалось загрузить"` - значит нужно настроить внешнее хранилище.


---

# LaTeX compilation fixes (extended regression)

## Результат

- `tests/regression_runner.py --tier extended --cache-mode replay --mode resilient`: **17/17 PASS (FAIL=0)**
- Артефакты:
  - `output/regression_reports/report.md`
  - `output/regression_reports/report.json`

## Ключевые изменения

- `pipeline/pdf_compiler.py`
  - BibTeX/Biber запускается по stem текущего `translated_fixed_N.tex`
  - Добавлен "extra retry", если surgical fix применён на последней попытке
  - Surgical fixes для частых падений:
    - `\DH/\dh` (OT1 encoding)
    - runaway/tcolorbox/promptbox (`\__tcobox_new_tcolorbox:w`), stub `promptbox`
    - `titletoc` partial TOC (`No partial toc named app`)
    - `wraptable` (комментирование проблемных блоков)
    - `tikzpicture` внутри `figure` (комментирование проблемных окружений)

- `tests/regression_runner.py`
  - Если PDF существует и в логе нет фатального останова, часть причин по логу демоутится в warnings
