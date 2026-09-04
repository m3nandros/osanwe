# Техническое задание: Система перевода PDF научных статей с сохранением layout

## 1. Общее описание системы

### 1.1 Цель проекта
Создать систему, которая принимает на вход PDF-файл научной статьи на английском языке и генерирует переведённый PDF на целевом языке с максимально точным сохранением визуального оформления оригинала.

### 1.2 Ключевая концепция
**PDF → Layout Analysis → LaTeX Template + Translated Text → PDF**

Система НЕ модифицирует исходный PDF напрямую. Вместо этого:
1. Извлекает полную геометрию и структуру документа
2. Извлекает все визуальные элементы (изображения, логотипы, линии, боксы)
3. Генерирует LaTeX-шаблон, воспроизводящий геометрию оригинала
4. Переводит текстовый контент
5. Компилирует LaTeX → PDF

Преимущество: LaTeX автоматически выполняет reflow текста (перенос строк, перенос на следующие страницы) в рамках заданных геометрических constraints.

### 1.3 Scope первой версии
- **Входной язык**: английский
- **Целевые языки**: русский, китайский, японский, корейский, немецкий, французский, испанский
- **Тип документов**: научные статьи (single-column и multi-column layouts)
- **Режим использования**: перевод для чтения (reading mode), НЕ для публикации

---

## 2. Архитектура системы

### 2.1 Общая схема pipeline

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              INPUT                                       │
│                         PDF-файл статьи                                  │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    МОДУЛЬ 0: LAYOUT DETECTOR (DocLayout-YOLO)            │
│  ML-детекция всех элементов документа: text blocks, tables, figures,    │
│  formulas, titles, abstracts, footnotes, headers, footers               │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    МОДУЛЬ 0.5: CROSS-PAGE SEMANTIC ANALYZER              │
│  Анализ продолжающегося контента через границы страниц                  │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    МОДУЛЬ 1: GEOMETRY EXTRACTOR                          │
│  Извлечение геометрии документа: размеры страниц, margins, колонки      │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    МОДУЛЬ 2: ELEMENT EXTRACTOR                           │
│  Извлечение всех визуальных элементов: изображения, логотипы, линии,    │
│  боксы, декоративные элементы, header/footer                            │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    МОДУЛЬ 3: CONTENT EXTRACTOR                           │
│  Извлечение текстового контента с семантической структурой:             │
│  заголовки, параграфы, списки, формулы, таблицы, сноски, references     │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    МОДУЛЬ 4: FONT ANALYZER                                │
│  Анализ шрифтов: основной шрифт, заголовки, размеры, стили              │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    МОДУЛЬ 5: LATEX TEMPLATE GENERATOR                    │
│  Генерация LaTeX-шаблона на основе извлечённой геометрии                │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    МОДУЛЬ 6: TRANSLATOR                                  │
│  Перевод текстового контента с сохранением структуры                    │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    МОДУЛЬ 7: LATEX ASSEMBLER                             │
│  Сборка финального LaTeX-документа: шаблон + переведённый контент       │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    МОДУЛЬ 8: PDF COMPILER                                │
│  Компиляция LaTeX → PDF (XeLaTeX/LuaLaTeX для Unicode)                  │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                              OUTPUT                                      │
│                     Переведённый PDF-файл                                │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.1.1 Поток данных от Layout Detector

```
                    ┌──────────────────────────────────────┐
                    │        МОДУЛЬ 0: LAYOUT DETECTOR     │
                    │          (DocLayout-YOLO)            │
                    └──────────────────────────────────────┘
                                      │
         ┌────────────────────────────┼────────────────────────────┐
         │                            │                            │
         ▼                            ▼                            ▼
┌─────────────────┐        ┌─────────────────┐        ┌─────────────────┐
│ titles          │        │ figures         │        │ page_headers    │
│ abstracts       │        │ tables          │        │ page_footers    │
│ plain_text      │        │ isolate_formula │        │ page_numbers    │
│ references      │        │ figure_caption  │        └────────┬────────┘
│ footnotes       │        │ table_caption   │                 │
└────────┬────────┘        └────────┬────────┘                 │
         │                          │                          │
         ▼                          ▼                          ▼
┌─────────────────┐        ┌─────────────────┐        ┌─────────────────┐
│ МОДУЛЬ 3:       │        │ МОДУЛЬ 2:       │        │ МОДУЛЬ 1:       │
│ CONTENT         │        │ ELEMENT         │        │ GEOMETRY        │
│ EXTRACTOR       │        │ EXTRACTOR       │        │ EXTRACTOR       │
│                 │        │                 │        │                 │
│ Использует bbox │        │ Извлекает как   │        │ Определяет      │
│ для извлечения  │        │ absolute        │        │ margins по      │
│ текста          │        │ positioned      │        │ header/footer   │
│                 │        │ elements        │        │ bbox            │
└─────────────────┘        └─────────────────┘        └─────────────────┘
```

### 2.1.2 Интеграция Cross-Page Analyzer

```
┌─────────────────────────────────────────────────────────────────┐
│              МОДУЛЬ 0.5: CROSS-PAGE SEMANTIC ANALYZER           │
├─────────────────────────────────────────────────────────────────┤
│  INPUT: Detection results + PDF document                        │
│                                                                 │
│  АНАЛИЗИРУЕТ:                                                   │
│  • Текст в нижней части страницы N                              │
│  • Текст в верхней части страницы N+1                           │
│  • Признаки продолжения:                                        │
│    - Нет точки/восклицательного в конце                         │
│    - Следующая страница начинается с lowercase                  │
│    - Слово разбито дефисом                                      │
│    - Слово-продолжение (and, but, which, который...)            │
│                                                                 │
│  OUTPUT: SemanticUnits (объединённые параграфы для перевода)    │
└─────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────┐
│                    МОДУЛЬ 6: TRANSLATOR                          │
│  Переводит SemanticUnits как единое целое, затем разбивает      │
│  обратно для позиционирования на соответствующих страницах      │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 Структура проекта (предлагаемая)

```
pdf-translator/
├── src/
│   ├── __init__.py
│   ├── main.py                    # Точка входа, CLI
│   ├── pipeline.py                # Координация всех модулей
│   │
│   ├── detection/
│   │   ├── __init__.py
│   │   ├── layout_detector.py     # Модуль 0: DocLayout-YOLO
│   │   └── cross_page_analyzer.py # Модуль 0.5: Cross-Page Analysis
│   │
│   ├── extractors/
│   │   ├── __init__.py
│   │   ├── geometry_extractor.py  # Модуль 1
│   │   ├── element_extractor.py   # Модуль 2
│   │   ├── content_extractor.py   # Модуль 3
│   │   └── font_analyzer.py       # Модуль 4
│   │
│   ├── generators/
│   │   ├── __init__.py
│   │   ├── template_generator.py  # Модуль 5
│   │   ├── latex_assembler.py     # Модуль 7
│   │   └── pdf_compiler.py        # Модуль 8
│   │
│   ├── translation/
│   │   ├── __init__.py
│   │   └── translator.py          # Модуль 6
│   │
│   ├── models/
│   │   ├── __init__.py
│   │   ├── document.py            # Dataclass для документа
│   │   ├── geometry.py            # Dataclass для геометрии
│   │   ├── elements.py            # Dataclass для элементов
│   │   └── content.py             # Dataclass для контента
│   │
│   └── utils/
│       ├── __init__.py
│       ├── pdf_utils.py
│       ├── latex_utils.py
│       └── image_utils.py
│
├── templates/
│   └── base_article.tex           # Базовый LaTeX шаблон
│
├── assets/
│   └── fonts/                     # Шрифты для компиляции
│
├── tests/
│   └── ...
│
├── requirements.txt
└── README.md
```

---

## 3. МОДУЛЬ 0: Layout Detector (DocLayout-YOLO)

### 3.1 Назначение
ML-детекция всех структурных элементов документа с использованием специализированной модели DocLayout-YOLO-DocStructBench. Этот модуль является первым этапом обработки и предоставляет данные для всех последующих модулей.

### 3.2 Почему DocLayout-YOLO, а не простые эвристики

**Проблемы эвристического подхода:**
- Шрифты и размеры варьируются между журналами
- Сложно отличить заголовок секции от подзаголовка только по размеру
- Abstract может выглядеть как обычный параграф
- Формулы могут быть inline или display
- Footnotes vs обычный текст внизу страницы

**Преимущества DocLayout-YOLO:**
- Обучена на DocStructBench (большой датасет научных статей)
- Детектирует 10+ типов элементов с высокой точностью
- Работает с различными стилями журналов
- Возвращает bounding boxes с confidence scores

### 3.3 Поддерживаемые классы элементов

```python
DOCLAYOUT_YOLO_CLASSES = {
    0: "title",           # Заголовок документа
    1: "plain_text",      # Обычный текст/параграфы
    2: "abandon",         # Элементы для игнорирования
    3: "figure",          # Рисунки
    4: "figure_caption",  # Подписи к рисункам
    5: "table",           # Таблицы
    6: "table_caption",   # Подписи к таблицам
    7: "table_footnote",  # Сноски к таблицам
    8: "isolate_formula", # Display формулы (отдельные)
    9: "formula_caption", # Номера формул (1), (2)
    10: "page_header",    # Верхний колонтитул
    11: "page_footer",    # Нижний колонтитул
    12: "page_number",    # Номер страницы
    13: "abstract",       # Аннотация
    14: "reference",      # Список литературы
    15: "footnote",       # Сноски
}
```

### 3.4 Входные данные
- PDF-документ (через PyMuPDF для конвертации страниц в изображения)

### 3.5 Выходные данные

```python
@dataclass
class DetectedElement:
    """Один обнаруженный элемент на странице."""
    page_number: int
    class_id: int
    class_name: str
    
    # Bounding box (в координатах изображения, нужна конвертация в PDF points)
    bbox: BoundingBox  # x0, y0, x1, y1
    
    # Уверенность модели
    confidence: float  # 0.0 - 1.0
    
    # Дополнительная информация
    area: float        # Площадь bbox
    aspect_ratio: float  # width / height

@dataclass
class PageDetectionResult:
    """Результат детекции для одной страницы."""
    page_number: int
    image_width: int
    image_height: int
    pdf_width_pt: float
    pdf_height_pt: float
    
    elements: List[DetectedElement]
    
    # Сгруппированные элементы по типу (для удобства)
    titles: List[DetectedElement]
    paragraphs: List[DetectedElement]
    figures: List[DetectedElement]
    figure_captions: List[DetectedElement]
    tables: List[DetectedElement]
    table_captions: List[DetectedElement]
    formulas: List[DetectedElement]
    headers: List[DetectedElement]
    footers: List[DetectedElement]
    footnotes: List[DetectedElement]
    references: List[DetectedElement]
    abstracts: List[DetectedElement]

@dataclass 
class DocumentDetectionResult:
    """Результат детекции для всего документа."""
    pages: List[PageDetectionResult]
    
    # Статистика
    total_elements: int
    elements_by_type: Dict[str, int]
```

### 3.6 Алгоритм работы

```python
from doclayout_yolo import YOLOv10

class LayoutDetector:
    """
    Детектор layout элементов на основе DocLayout-YOLO.
    """
    
    def __init__(self, model_path: str = "doclayout_yolo_docstructbench.pt"):
        """
        Инициализация модели.
        
        Args:
            model_path: Путь к весам модели. 
                        Можно скачать с HuggingFace: opendatalab/DocLayout-YOLO
        """
        self.model = YOLOv10(model_path)
        
        # Маппинг классов
        self.class_names = {
            0: "title", 1: "plain_text", 2: "abandon", 3: "figure",
            4: "figure_caption", 5: "table", 6: "table_caption",
            7: "table_footnote", 8: "isolate_formula", 9: "formula_caption",
            10: "page_header", 11: "page_footer", 12: "page_number",
            13: "abstract", 14: "reference", 15: "footnote"
        }
    
    def detect_document(
        self, 
        doc: fitz.Document,
        confidence_threshold: float = 0.25,
        dpi: int = 150
    ) -> DocumentDetectionResult:
        """
        Детектирует элементы во всём документе.
        
        Args:
            doc: PyMuPDF Document объект
            confidence_threshold: Минимальная уверенность для включения элемента
            dpi: DPI для рендеринга страниц (выше = точнее, но медленнее)
        
        Returns:
            DocumentDetectionResult с детекциями для всех страниц
        """
        page_results = []
        
        for page_num, page in enumerate(doc):
            result = self.detect_page(page, page_num, confidence_threshold, dpi)
            page_results.append(result)
        
        # Собираем статистику
        total = sum(len(p.elements) for p in page_results)
        by_type = {}
        for p in page_results:
            for elem in p.elements:
                by_type[elem.class_name] = by_type.get(elem.class_name, 0) + 1
        
        return DocumentDetectionResult(
            pages=page_results,
            total_elements=total,
            elements_by_type=by_type
        )
    
    def detect_page(
        self,
        page: fitz.Page,
        page_number: int,
        confidence_threshold: float,
        dpi: int
    ) -> PageDetectionResult:
        """
        Детектирует элементы на одной странице.
        """
        # Рендерим страницу в изображение
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=mat)
        
        # Конвертируем в numpy array для YOLO
        import numpy as np
        img = np.frombuffer(pix.samples, dtype=np.uint8)
        img = img.reshape(pix.height, pix.width, pix.n)
        
        if pix.n == 4:  # RGBA -> RGB
            img = img[:, :, :3]
        
        # Запускаем детекцию
        results = self.model.predict(img, conf=confidence_threshold)
        
        # Парсим результаты
        elements = []
        for box in results[0].boxes:
            class_id = int(box.cls[0])
            class_name = self.class_names.get(class_id, "unknown")
            
            # Пропускаем "abandon" элементы
            if class_name == "abandon":
                continue
            
            # Конвертируем координаты из image space в PDF points
            x0, y0, x1, y1 = box.xyxy[0].tolist()
            scale_x = page.rect.width / pix.width
            scale_y = page.rect.height / pix.height
            
            bbox = BoundingBox(
                x0=x0 * scale_x,
                y0=y0 * scale_y,
                x1=x1 * scale_x,
                y1=y1 * scale_y
            )
            
            elements.append(DetectedElement(
                page_number=page_number,
                class_id=class_id,
                class_name=class_name,
                bbox=bbox,
                confidence=float(box.conf[0]),
                area=bbox.width * bbox.height,
                aspect_ratio=bbox.width / bbox.height if bbox.height > 0 else 0
            ))
        
        # Группируем по типам
        return PageDetectionResult(
            page_number=page_number,
            image_width=pix.width,
            image_height=pix.height,
            pdf_width_pt=page.rect.width,
            pdf_height_pt=page.rect.height,
            elements=elements,
            titles=[e for e in elements if e.class_name == "title"],
            paragraphs=[e for e in elements if e.class_name == "plain_text"],
            figures=[e for e in elements if e.class_name == "figure"],
            figure_captions=[e for e in elements if e.class_name == "figure_caption"],
            tables=[e for e in elements if e.class_name == "table"],
            table_captions=[e for e in elements if e.class_name == "table_caption"],
            formulas=[e for e in elements if e.class_name in ["isolate_formula", "formula_caption"]],
            headers=[e for e in elements if e.class_name == "page_header"],
            footers=[e for e in elements if e.class_name == "page_footer"],
            footnotes=[e for e in elements if e.class_name in ["footnote", "table_footnote"]],
            references=[e for e in elements if e.class_name == "reference"],
            abstracts=[e for e in elements if e.class_name == "abstract"]
        )
```

### 3.7 Связь с другими модулями

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    МОДУЛЬ 0: LAYOUT DETECTOR                             │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
        ┌───────────────────────────┼───────────────────────────┐
        │                           │                           │
        ▼                           ▼                           ▼
┌───────────────┐         ┌───────────────────┐       ┌───────────────────┐
│ МОДУЛЬ 1:     │         │ МОДУЛЬ 2:         │       │ МОДУЛЬ 3:         │
│ GEOMETRY      │         │ ELEMENT           │       │ CONTENT           │
│ EXTRACTOR     │         │ EXTRACTOR         │       │ EXTRACTOR         │
├───────────────┤         ├───────────────────┤       ├───────────────────┤
│ Использует:   │         │ Использует:       │       │ Использует:       │
│ • headers     │         │ • figures         │       │ • titles          │
│ • footers     │         │ • tables          │       │ • paragraphs      │
│ • page_number │         │ • formulas        │       │ • abstracts       │
│ для опреде-   │         │ • figure_captions │       │ • references      │
│ ления margins │         │ для извлечения    │       │ • footnotes       │
│ и layout      │         │ как absolute      │       │ для извлечения    │
│               │         │ elements          │       │ текста            │
└───────────────┘         └───────────────────┘       └───────────────────┘
```

### 3.8 Установка DocLayout-YOLO

```bash
# Установка через pip
pip install doclayout-yolo

# Или из репозитория для последней версии
pip install git+https://github.com/opendatalab/DocLayout-YOLO.git

# Скачивание весов модели
# Модель: DocLayout-YOLO-DocStructBench (~25MB)
# Доступна на HuggingFace: https://huggingface.co/opendatalab/DocLayout-YOLO
```

### 3.9 Edge Cases для Layout Detector

| Edge Case | Описание | Решение |
|-----------|----------|---------|
| Low confidence detections | Элементы с низкой уверенностью | Использовать threshold 0.25, но логировать элементы 0.1-0.25 |
| Overlapping detections | Перекрывающиеся bounding boxes | Non-maximum suppression (NMS) встроен в YOLO |
| Missed elements | Модель не нашла очевидный элемент | Fallback на эвристики для критичных элементов (title) |
| Wrong classification | Неверный класс (caption vs paragraph) | Пост-обработка с учётом позиции относительно figures |
| Multi-column confusion | Смешение колонок | Использовать геометрию колонок из Geometry Extractor |
| Small fonts/elements | Элементы не детектируются из-за низкого DPI | Увеличить DPI до 200-300 для проблемных страниц |
| Scanned PDFs | Артефакты сканирования | Pre-processing: denoise, deskew |
| Rotated pages | Страницы с rotation | Применить rotation перед детекцией |

---

## 4. МОДУЛЬ 0.5: Cross-Page Semantic Analyzer

### 4.1 Назначение
Анализ контента, который продолжается через границы страниц. Это критически важно для правильного перевода, так как параграф, разбитый на две страницы, должен переводиться как единое целое для сохранения контекста.

### 4.2 Типы cross-page continuity

```python
class ContinuityType(Enum):
    PARAGRAPH = "paragraph"       # Параграф продолжается
    SENTENCE = "sentence"         # Предложение продолжается
    FOOTNOTE = "footnote"         # Сноска продолжается
    TABLE = "table"               # Таблица продолжается
    REFERENCE_LIST = "references" # Список литературы продолжается
    FIGURE_CAPTION = "caption"    # Подпись к рисунку продолжается
```

### 4.3 Структура данных

```python
@dataclass
class CrossPageLink:
    """Связь между элементами на разных страницах."""
    
    # Элемент на предыдущей странице
    source_page: int
    source_element_index: int
    source_element_type: str
    source_text_end: str  # Последние ~50 символов
    
    # Элемент на следующей странице
    target_page: int
    target_element_index: int
    target_element_type: str
    target_text_start: str  # Первые ~50 символов
    
    # Тип связи
    continuity_type: ContinuityType
    confidence: float  # Уверенность в связи
    
    # Признаки, по которым определена связь
    indicators: List[str]  # ["no_period", "lowercase_start", "same_column"]

@dataclass
class SemanticUnit:
    """Семантическая единица (может span multiple pages)."""
    unit_id: str
    unit_type: str  # "paragraph", "section", "footnote", etc.
    
    # Список элементов, составляющих эту единицу
    elements: List[Tuple[int, int]]  # (page_number, element_index)
    
    # Полный текст (объединённый)
    full_text: str
    
    # Маркеры продолжения
    continues_from_previous: bool
    continues_to_next: bool

@dataclass
class CrossPageAnalysisResult:
    """Результат анализа cross-page continuity."""
    links: List[CrossPageLink]
    semantic_units: List[SemanticUnit]
    
    # Карта: (page, element_index) -> semantic_unit_id
    element_to_unit: Dict[Tuple[int, int], str]
```

### 4.4 Алгоритм анализа

```python
class CrossPageAnalyzer:
    """
    Анализатор продолжения контента через границы страниц.
    """
    
    def __init__(self):
        # Паттерны для определения продолжения
        self.sentence_end_pattern = re.compile(r'[.!?]["\'»]?\s*$')
        self.sentence_start_pattern = re.compile(r'^[A-ZА-ЯЁ"]')
        
        # Слова, которые обычно не начинают предложение
        self.continuation_words = {
            'and', 'or', 'but', 'so', 'yet', 'nor',  # English
            'и', 'или', 'но', 'а', 'однако',         # Russian
            'which', 'that', 'who', 'whom',
            'который', 'которая', 'которое', 'которые'
        }
    
    def analyze(
        self,
        detection_result: DocumentDetectionResult,
        doc: fitz.Document
    ) -> CrossPageAnalysisResult:
        """
        Анализирует весь документ на предмет cross-page continuity.
        """
        links = []
        
        for page_num in range(len(detection_result.pages) - 1):
            current_page = detection_result.pages[page_num]
            next_page = detection_result.pages[page_num + 1]
            
            # Анализируем связи между страницами
            page_links = self._analyze_page_pair(
                current_page, next_page, doc, page_num
            )
            links.extend(page_links)
        
        # Строим семантические единицы
        semantic_units = self._build_semantic_units(links, detection_result, doc)
        
        # Строим карту element -> unit
        element_to_unit = {}
        for unit in semantic_units:
            for page, idx in unit.elements:
                element_to_unit[(page, idx)] = unit.unit_id
        
        return CrossPageAnalysisResult(
            links=links,
            semantic_units=semantic_units,
            element_to_unit=element_to_unit
        )
    
    def _analyze_page_pair(
        self,
        current_page: PageDetectionResult,
        next_page: PageDetectionResult,
        doc: fitz.Document,
        current_page_num: int
    ) -> List[CrossPageLink]:
        """
        Анализирует пару соседних страниц.
        """
        links = []
        
        # Получаем элементы внизу текущей страницы и вверху следующей
        bottom_elements = self._get_bottom_elements(current_page)
        top_elements = self._get_top_elements(next_page)
        
        for bottom_elem in bottom_elements:
            # Извлекаем текст элемента
            bottom_text = self._extract_element_text(
                doc[current_page_num], bottom_elem
            )
            
            if not bottom_text:
                continue
            
            # Проверяем признаки продолжения
            indicators = []
            
            # 1. Нет точки/восклицательного/вопросительного в конце
            if not self.sentence_end_pattern.search(bottom_text):
                indicators.append("no_sentence_end")
            
            # 2. Заканчивается на дефис (перенос слова)
            if bottom_text.rstrip().endswith('-'):
                indicators.append("hyphenation")
            
            # Ищем соответствующий элемент на следующей странице
            for top_elem in top_elements:
                top_text = self._extract_element_text(
                    doc[current_page_num + 1], top_elem
                )
                
                if not top_text:
                    continue
                
                top_indicators = list(indicators)
                
                # 3. Начинается с маленькой буквы
                if top_text and top_text[0].islower():
                    top_indicators.append("lowercase_start")
                
                # 4. Начинается со слова-продолжения
                first_word = top_text.split()[0].lower() if top_text.split() else ""
                if first_word in self.continuation_words:
                    top_indicators.append("continuation_word")
                
                # 5. Тот же тип элемента
                if bottom_elem.class_name == top_elem.class_name:
                    top_indicators.append("same_element_type")
                
                # 6. Та же колонка (для multi-column)
                if self._same_column(bottom_elem, top_elem, current_page):
                    top_indicators.append("same_column")
                
                # Вычисляем уверенность
                confidence = self._compute_confidence(top_indicators)
                
                if confidence > 0.5:
                    links.append(CrossPageLink(
                        source_page=current_page_num,
                        source_element_index=bottom_elements.index(bottom_elem),
                        source_element_type=bottom_elem.class_name,
                        source_text_end=bottom_text[-50:] if len(bottom_text) > 50 else bottom_text,
                        target_page=current_page_num + 1,
                        target_element_index=top_elements.index(top_elem),
                        target_element_type=top_elem.class_name,
                        target_text_start=top_text[:50] if len(top_text) > 50 else top_text,
                        continuity_type=self._determine_continuity_type(
                            bottom_elem, top_elem, top_indicators
                        ),
                        confidence=confidence,
                        indicators=top_indicators
                    ))
                    break  # Нашли связь, не ищем дальше
        
        return links
    
    def _get_bottom_elements(
        self, 
        page: PageDetectionResult,
        threshold_ratio: float = 0.15
    ) -> List[DetectedElement]:
        """
        Получает элементы в нижней части страницы.
        
        threshold_ratio: доля страницы от низа для поиска (0.15 = нижние 15%)
        """
        page_height = page.pdf_height_pt
        threshold_y = page_height * (1 - threshold_ratio)
        
        # Исключаем headers, footers, page numbers
        excluded_types = {"page_header", "page_footer", "page_number"}
        
        bottom = [
            e for e in page.elements
            if e.bbox.y1 > threshold_y and e.class_name not in excluded_types
        ]
        
        # Сортируем по позиции (снизу вверх)
        return sorted(bottom, key=lambda e: -e.bbox.y1)
    
    def _get_top_elements(
        self,
        page: PageDetectionResult,
        threshold_ratio: float = 0.15
    ) -> List[DetectedElement]:
        """
        Получает элементы в верхней части страницы.
        """
        page_height = page.pdf_height_pt
        threshold_y = page_height * threshold_ratio
        
        excluded_types = {"page_header", "page_footer", "page_number"}
        
        top = [
            e for e in page.elements
            if e.bbox.y0 < threshold_y and e.class_name not in excluded_types
        ]
        
        # Сортируем по позиции (сверху вниз)
        return sorted(top, key=lambda e: e.bbox.y0)
    
    def _same_column(
        self,
        elem1: DetectedElement,
        elem2: DetectedElement,
        page: PageDetectionResult
    ) -> bool:
        """
        Проверяет, находятся ли элементы в одной колонке.
        """
        # Простая эвристика: центры по X близки
        center1_x = (elem1.bbox.x0 + elem1.bbox.x1) / 2
        center2_x = (elem2.bbox.x0 + elem2.bbox.x1) / 2
        
        # Допуск: 10% от ширины страницы
        tolerance = page.pdf_width_pt * 0.1
        
        return abs(center1_x - center2_x) < tolerance
    
    def _compute_confidence(self, indicators: List[str]) -> float:
        """
        Вычисляет уверенность в связи на основе индикаторов.
        """
        weights = {
            "no_sentence_end": 0.3,
            "hyphenation": 0.5,
            "lowercase_start": 0.4,
            "continuation_word": 0.3,
            "same_element_type": 0.2,
            "same_column": 0.1,
        }
        
        score = sum(weights.get(ind, 0) for ind in indicators)
        return min(score, 1.0)
    
    def _determine_continuity_type(
        self,
        source: DetectedElement,
        target: DetectedElement,
        indicators: List[str]
    ) -> ContinuityType:
        """
        Определяет тип продолжения.
        """
        if source.class_name == "footnote":
            return ContinuityType.FOOTNOTE
        elif source.class_name == "table":
            return ContinuityType.TABLE
        elif source.class_name == "reference":
            return ContinuityType.REFERENCE_LIST
        elif source.class_name == "figure_caption":
            return ContinuityType.FIGURE_CAPTION
        elif "no_sentence_end" in indicators:
            return ContinuityType.SENTENCE
        else:
            return ContinuityType.PARAGRAPH
    
    def _build_semantic_units(
        self,
        links: List[CrossPageLink],
        detection_result: DocumentDetectionResult,
        doc: fitz.Document
    ) -> List[SemanticUnit]:
        """
        Строит семантические единицы из связей.
        """
        # Используем Union-Find для группировки связанных элементов
        # ... реализация
        pass
    
    def _extract_element_text(
        self,
        page: fitz.Page,
        element: DetectedElement
    ) -> str:
        """
        Извлекает текст из области элемента.
        """
        rect = fitz.Rect(
            element.bbox.x0,
            element.bbox.y0,
            element.bbox.x1,
            element.bbox.y1
        )
        return page.get_text("text", clip=rect).strip()
```

### 4.5 Использование результатов Cross-Page Analysis

```python
# В модуле Translator

def translate_with_cross_page_context(
    content: DocumentContent,
    cross_page_result: CrossPageAnalysisResult,
    translator: TranslationAPI
) -> DocumentContent:
    """
    Переводит контент с учётом cross-page continuity.
    
    Если параграф разбит на две страницы:
    1. Объединяем текст из обеих частей
    2. Переводим как единое целое
    3. Разбиваем обратно (примерно в той же пропорции)
    """
    
    for unit in cross_page_result.semantic_units:
        if len(unit.elements) > 1:  # Multi-page unit
            # Переводим полный текст
            translated_full = translator.translate(unit.full_text)
            
            # Разбиваем обратно
            # (логика разбиения с сохранением пропорций)
            pass
```

### 4.6 Edge Cases для Cross-Page Analyzer

| Edge Case | Описание | Решение |
|-----------|----------|---------|
| False positive | Два несвязанных параграфа определены как продолжение | Использовать высокий threshold confidence |
| Multiple columns | Контент из разных колонок | Учитывать x-координаты при матчинге |
| Footnote continuation | Сноска продолжается на следующей странице | Отдельная логика для footnotes |
| List continuation | Нумерованный список продолжается | Детектировать по паттерну нумерации |
| Hyphenated words | Слово разбито дефисом на границе страницы | Объединять и убирать дефис при переводе |
| Different languages | Цитата на другом языке продолжается | Детектировать язык, не переводить |

---

## 5. МОДУЛЬ 1: Geometry Extractor

### 5.1 Назначение
Извлечение базовой геометрии документа: размеры страниц, отступы (margins), структура колонок.

### 5.2 Входные данные
- Путь к PDF-файлу

### 5.3 Выходные данные
Объект `DocumentGeometry` со следующей структурой:

```python
@dataclass
class PageGeometry:
    page_number: int
    width_pt: float           # Ширина страницы в points (1 pt = 1/72 inch)
    height_pt: float          # Высота страницы в points
    
    # Margins (отступы от края страницы до текстовой области)
    margin_top_pt: float
    margin_bottom_pt: float
    margin_left_pt: float
    margin_right_pt: float
    
    # Структура колонок (если multi-column)
    num_columns: int          # 1 для single-column, 2 для two-column
    column_width_pt: float    # Ширина одной колонки
    column_gap_pt: float      # Расстояние между колонками (gutter)
    
    # Header/Footer области
    header_height_pt: float   # Высота области header (от top margin вверх)
    footer_height_pt: float   # Высота области footer (от bottom margin вниз)
    
    # Флаги особенностей страницы
    is_title_page: bool       # Титульная страница (обычно имеет особый layout)
    has_different_first_page: bool  # Отличается ли первая страница от остальных

@dataclass
class DocumentGeometry:
    pages: List[PageGeometry]
    
    # Общие параметры документа
    paper_format: str         # "a4", "letter", "custom"
    paper_width_pt: float
    paper_height_pt: float
    
    # Преобладающая структура (для основных страниц)
    default_num_columns: int
    default_margins: Dict[str, float]  # {"top": x, "bottom": y, "left": z, "right": w}
```

### 5.4 Алгоритм извлечения

#### 5.4.1 Определение размеров страницы
```python
import fitz  # PyMuPDF

def extract_page_size(page: fitz.Page) -> Tuple[float, float]:
    """
    Извлекает размеры страницы в points.
    
    ВАЖНО: В PDF координата (0, 0) находится в левом нижнем углу,
    но PyMuPDF автоматически преобразует в left-top origin.
    """
    rect = page.rect
    return rect.width, rect.height
```

#### 5.4.2 Определение margins через анализ text blocks
```python
def detect_margins(page: fitz.Page) -> Dict[str, float]:
    """
    Определяет margins путём анализа bounding boxes всех текстовых блоков.
    
    Алгоритм:
    1. Получить все text blocks на странице
    2. Найти минимальные/максимальные координаты текста
    3. Margins = расстояние от края страницы до крайних текстовых блоков
    
    EDGE CASE: На титульной странице могут быть элементы за пределами 
    стандартных margins (логотипы, метаинформация сбоку). Их нужно 
    исключить из расчёта margins основного текста.
    """
    blocks = page.get_text("dict")["blocks"]
    
    text_blocks = [b for b in blocks if b["type"] == 0]  # type 0 = text
    
    if not text_blocks:
        return {"top": 72, "bottom": 72, "left": 72, "right": 72}  # defaults
    
    # Находим границы текстовой области
    min_x = min(b["bbox"][0] for b in text_blocks)
    min_y = min(b["bbox"][1] for b in text_blocks)
    max_x = max(b["bbox"][2] for b in text_blocks)
    max_y = max(b["bbox"][3] for b in text_blocks)
    
    page_width, page_height = page.rect.width, page.rect.height
    
    return {
        "left": min_x,
        "top": min_y,
        "right": page_width - max_x,
        "bottom": page_height - max_y
    }
```

#### 5.4.3 Определение количества колонок
```python
def detect_columns(page: fitz.Page) -> Tuple[int, float, float]:
    """
    Определяет структуру колонок на странице.
    
    Алгоритм:
    1. Получить все text blocks
    2. Построить гистограмму x-координат левых границ блоков
    3. Если есть два явных пика — two-column layout
    4. Найти gap между колонками
    
    Возвращает: (num_columns, column_width, column_gap)
    
    EDGE CASE: На титульной странице может быть single-column даже
    если остальной документ two-column. Нужно анализировать каждую
    страницу отдельно, но также определить "преобладающий" layout.
    """
    blocks = page.get_text("dict")["blocks"]
    text_blocks = [b for b in blocks if b["type"] == 0]
    
    if len(text_blocks) < 3:
        return (1, 0, 0)
    
    # Группируем блоки по x-позиции левой границы
    left_positions = [b["bbox"][0] for b in text_blocks]
    
    # Кластеризация позиций (простой алгоритм)
    # Если позиции группируются вокруг двух центров — two-column
    sorted_positions = sorted(set(int(p) for p in left_positions))
    
    # Находим большие разрывы в позициях
    gaps = []
    for i in range(1, len(sorted_positions)):
        gap = sorted_positions[i] - sorted_positions[i-1]
        if gap > 50:  # threshold в points
            gaps.append((sorted_positions[i-1], sorted_positions[i], gap))
    
    if gaps:
        # Two-column layout
        largest_gap = max(gaps, key=lambda x: x[2])
        column_gap = largest_gap[2]
        # Вычисляем ширину колонки
        # ...
        return (2, column_width, column_gap)
    else:
        return (1, 0, 0)
```

#### 5.4.4 Определение header/footer областей
```python
def detect_header_footer(page: fitz.Page, margins: Dict) -> Tuple[float, float]:
    """
    Определяет высоту header и footer областей.
    
    Header: область от верха страницы до top margin, содержащая
    повторяющиеся элементы (номер страницы, название журнала, etc.)
    
    Footer: область от bottom margin до низа страницы.
    
    Алгоритм:
    1. Искать текстовые блоки выше top margin → header
    2. Искать текстовые блоки ниже bottom margin → footer
    3. Анализировать, повторяются ли эти блоки на других страницах
    """
    blocks = page.get_text("dict")["blocks"]
    page_height = page.rect.height
    
    header_blocks = [b for b in blocks 
                     if b["type"] == 0 and b["bbox"][3] < margins["top"]]
    footer_blocks = [b for b in blocks 
                     if b["type"] == 0 and b["bbox"][1] > page_height - margins["bottom"]]
    
    header_height = max((b["bbox"][3] for b in header_blocks), default=0)
    footer_height = max((page_height - b["bbox"][1] for b in footer_blocks), default=0)
    
    return header_height, footer_height
```

### 5.5 Edge Cases для Geometry Extractor

| Edge Case | Описание | Решение |
|-----------|----------|---------|
| Разные размеры страниц | Некоторые PDF имеют страницы разного размера | Обрабатывать каждую страницу отдельно, определить "основной" размер |
| Титульная страница | Отличается от остальных (single-column, особые margins) | Флаг `is_title_page`, отдельная обработка |
| Landscape orientation | Страницы в альбомной ориентации | Определять orientation через сравнение width/height |
| Нестандартные margins | Журналы с боковыми панелями (как Frontiers) | Разделять "текстовую область" и "служебные области" |
| Переменные margins | Margins меняются от страницы к странице | Определять margins для каждой страницы, но также вычислять "типичные" |
| Rotated pages | Страницы с rotation != 0 | Использовать `page.rotation` для нормализации |

---

## 6. МОДУЛЬ 2: Element Extractor

### 6.1 Назначение
Извлечение всех визуальных элементов документа, которые должны быть позиционированы absolute (независимо от текста).

### 6.2 Типы элементов

#### 6.2.1 Изображения (Images)
```python
@dataclass
class ImageElement:
    page_number: int
    
    # Bounding box в координатах страницы (points)
    x: float              # Left
    y: float              # Top
    width: float
    height: float
    
    # Данные изображения
    image_data: bytes     # Raw image bytes
    image_format: str     # "png", "jpeg", etc.
    
    # Метаданные
    dpi: int              # DPI изображения
    
    # Семантическая информация
    element_type: str     # "figure", "logo", "icon", "decorative"
    
    # Позиционирование в LaTeX
    positioning: str      # "absolute" для логотипов, "float" для figures
```

#### 6.2.2 Линии и графические примитивы
```python
@dataclass
class LineElement:
    page_number: int
    
    # Координаты линии
    x1: float
    y1: float
    x2: float
    y2: float
    
    # Стиль
    stroke_width: float   # Толщина линии в points
    stroke_color: str     # Цвет в hex (#000000)
    
    # Тип линии
    line_type: str        # "horizontal", "vertical", "diagonal"
    
    # Семантика
    semantic_role: str    # "title_underline", "section_separator", 
                          # "header_line", "footer_line", "decorative"
```

#### 6.2.3 Прямоугольники и боксы
```python
@dataclass
class BoxElement:
    page_number: int
    
    # Bounding box
    x: float
    y: float
    width: float
    height: float
    
    # Стиль
    stroke_width: float
    stroke_color: str
    fill_color: Optional[str]  # None если без заливки
    
    # Семантика
    semantic_role: str    # "abstract_box", "warning_box", "sidebar", 
                          # "title_box", "info_panel"
```

#### 6.2.4 Header/Footer контент
```python
@dataclass
class HeaderFooterElement:
    page_number: int
    position: str         # "header" или "footer"
    
    # Контент
    content: str          # Текст (может содержать {{page_number}} placeholder)
    
    # Позиция
    x: float
    y: float
    
    # Стиль текста
    font_size: float
    font_family: str
    font_style: str       # "normal", "italic", "bold"
    alignment: str        # "left", "center", "right"
```

### 6.3 Алгоритм извлечения

#### 6.3.1 Извлечение изображений
```python
def extract_images(doc: fitz.Document) -> List[ImageElement]:
    """
    Извлекает все изображения из документа.
    
    ВАЖНО: Необходимо различать:
    - Figures (научные рисунки) — позиционируются как float
    - Logos (логотипы журнала) — позиционируются absolute
    - Icons/decorative — позиционируются absolute
    
    Эвристики для классификации:
    - Logos: обычно на титульной странице, в header/footer области,
      маленького размера, вне основной текстовой области
    - Figures: в основной текстовой области, большого размера,
      обычно имеют caption рядом
    """
    images = []
    
    for page_num, page in enumerate(doc):
        # Получаем список изображений на странице
        image_list = page.get_images(full=True)
        
        for img_index, img_info in enumerate(image_list):
            xref = img_info[0]
            
            # Получаем bounding box изображения
            # (требуется дополнительная обработка)
            image_rects = page.get_image_rects(xref)
            
            for rect in image_rects:
                # Извлекаем данные изображения
                base_image = doc.extract_image(xref)
                
                # Классифицируем тип элемента
                element_type = classify_image_type(
                    rect, page.rect, page_num == 0
                )
                
                images.append(ImageElement(
                    page_number=page_num,
                    x=rect.x0,
                    y=rect.y0,
                    width=rect.width,
                    height=rect.height,
                    image_data=base_image["image"],
                    image_format=base_image["ext"],
                    dpi=base_image.get("xres", 72),
                    element_type=element_type,
                    positioning="absolute" if element_type in ["logo", "icon"] else "float"
                ))
    
    return images

def classify_image_type(rect, page_rect, is_first_page: bool) -> str:
    """
    Классифицирует тип изображения по его позиции и размеру.
    """
    # Относительные координаты
    rel_x = rect.x0 / page_rect.width
    rel_y = rect.y0 / page_rect.height
    rel_width = rect.width / page_rect.width
    rel_height = rect.height / page_rect.height
    
    # Logo detection heuristics
    if is_first_page:
        # Верхняя часть страницы, маленький размер
        if rel_y < 0.15 and rel_width < 0.3 and rel_height < 0.1:
            return "logo"
        # Боковые области
        if rel_x < 0.1 or rel_x > 0.8:
            if rel_width < 0.2:
                return "logo"
    
    # Decorative/icon
    if rel_width < 0.05 and rel_height < 0.05:
        return "icon"
    
    # По умолчанию — figure
    return "figure"
```

#### 6.3.2 Извлечение линий и графических примитивов
```python
def extract_lines_and_shapes(page: fitz.Page) -> Tuple[List[LineElement], List[BoxElement]]:
    """
    Извлекает линии и прямоугольники из PDF.
    
    PyMuPDF позволяет извлечь paths (vector graphics) через get_drawings().
    
    ВАЖНО: Многие PDF не содержат отдельных линий — они могут быть
    частью изображений или отрендерены как thin rectangles.
    """
    lines = []
    boxes = []
    
    drawings = page.get_drawings()
    
    for path in drawings:
        if path["type"] == "l":  # line
            lines.append(LineElement(
                page_number=page.number,
                x1=path["rect"].x0,
                y1=path["rect"].y0,
                x2=path["rect"].x1,
                y2=path["rect"].y1,
                stroke_width=path.get("width", 1),
                stroke_color=color_to_hex(path.get("color")),
                line_type=classify_line_type(path["rect"]),
                semantic_role=determine_line_role(path, page)
            ))
        elif path["type"] == "re":  # rectangle
            boxes.append(BoxElement(
                page_number=page.number,
                x=path["rect"].x0,
                y=path["rect"].y0,
                width=path["rect"].width,
                height=path["rect"].height,
                stroke_width=path.get("width", 0),
                stroke_color=color_to_hex(path.get("color")),
                fill_color=color_to_hex(path.get("fill")),
                semantic_role=determine_box_role(path, page)
            ))
    
    return lines, boxes

def determine_line_role(path, page) -> str:
    """
    Определяет семантическую роль линии.
    
    Эвристики:
    - Горизонтальная линия под заголовком → "title_underline"
    - Линия в верхней части страницы → "header_line"
    - Линия в нижней части → "footer_line"
    - Линия между колонками → "column_separator"
    """
    rect = path["rect"]
    page_height = page.rect.height
    
    # Горизонтальная линия
    if abs(rect.y0 - rect.y1) < 2:  # threshold
        rel_y = rect.y0 / page_height
        
        if rel_y < 0.15:
            return "header_line"
        elif rel_y > 0.9:
            return "footer_line"
        elif rel_y < 0.25:
            return "title_underline"
        else:
            return "section_separator"
    
    return "decorative"
```

#### 6.3.3 Извлечение Header/Footer контента
```python
def extract_headers_footers(doc: fitz.Document, geometry: DocumentGeometry) -> List[HeaderFooterElement]:
    """
    Извлекает повторяющийся контент из header и footer областей.
    
    Алгоритм:
    1. Для каждой страницы найти текст в header/footer областях
    2. Найти паттерны (повторяющийся текст с вариациями номера страницы)
    3. Сгенерировать шаблоны с placeholders
    
    EDGE CASE: Первая страница часто не имеет header или имеет другой header.
    """
    headers_footers = []
    
    # Собираем header/footer контент со всех страниц
    header_contents = []
    footer_contents = []
    
    for page_num, page in enumerate(doc):
        page_geom = geometry.pages[page_num]
        
        # Извлекаем текст в header области
        header_rect = fitz.Rect(
            0, 0,
            page.rect.width, page_geom.margin_top
        )
        header_text = page.get_text("text", clip=header_rect).strip()
        header_contents.append(header_text)
        
        # Извлекаем текст в footer области
        footer_rect = fitz.Rect(
            0, page.rect.height - page_geom.margin_bottom,
            page.rect.width, page.rect.height
        )
        footer_text = page.get_text("text", clip=footer_rect).strip()
        footer_contents.append(footer_text)
    
    # Анализируем паттерны
    header_template = detect_template_with_page_number(header_contents)
    footer_template = detect_template_with_page_number(footer_contents)
    
    # ... создаём HeaderFooterElement объекты
    
    return headers_footers

def detect_template_with_page_number(contents: List[str]) -> Optional[str]:
    """
    Обнаруживает шаблон с номером страницы.
    
    Пример:
    ["", "Frontiers in Chemical Biology 02", "Frontiers in Chemical Biology 03"]
    → "Frontiers in Chemical Biology {{page_number}}"
    """
    # Ищем числа, которые увеличиваются на 1
    # Заменяем их на {{page_number}}
    # ...
    pass
```

### 6.4 Edge Cases для Element Extractor

| Edge Case | Описание | Решение |
|-----------|----------|---------|
| Embedded fonts as images | Некоторые символы рендерятся как изображения | Детектировать по размеру и пропорциям, пропускать |
| Watermarks | Водяные знаки | Детектировать по прозрачности, опционально пропускать |
| Background images | Фоновые изображения на всю страницу | Детектировать по размеру == page size, обрабатывать отдельно |
| Multi-part figures | Составные изображения (a, b, c, d) | Детектировать группы близко расположенных изображений |
| Inline images | Изображения внутри текста (иконки) | Детектировать по размеру < line height |
| QR codes | QR-коды на титульной странице | Классифицировать как "qr_code", позиционировать absolute |
| DOI badges | Плашки с DOI | Детектировать по характерным размерам и позиции |

---

## 7. МОДУЛЬ 3: Content Extractor

### 7.1 Назначение
Извлечение текстового контента с полной семантической структурой и связью с геометрией.

### 7.2 Структура контента

```python
@dataclass
class TextBlock:
    """Базовый блок текста."""
    text: str
    
    # Геометрия блока
    page_number: int
    bbox: Tuple[float, float, float, float]  # (x0, y0, x1, y1)
    
    # Стиль
    font_name: str
    font_size: float
    font_flags: int       # bold, italic, etc.
    color: str
    
    # Семантика
    block_type: str       # См. ниже

@dataclass
class DocumentContent:
    """Полный контент документа."""
    
    # Титульная страница
    title: str
    authors: List[AuthorInfo]
    affiliations: List[str]
    abstract: str
    keywords: List[str]
    
    # Метаинформация (не переводится)
    doi: Optional[str]
    journal_name: Optional[str]
    received_date: Optional[str]
    accepted_date: Optional[str]
    published_date: Optional[str]
    
    # Основной контент
    sections: List[Section]
    
    # Элементы вне основного потока
    figures: List[Figure]
    tables: List[Table]
    equations: List[Equation]
    footnotes: List[Footnote]
    
    # Список литературы (НЕ ПЕРЕВОДИТСЯ)
    references: List[Reference]
    
    # Acknowledgements (опционально переводится)
    acknowledgements: Optional[str]

@dataclass
class Section:
    """Секция документа."""
    level: int            # 1 = H1, 2 = H2, etc.
    title: str
    content: List[Union[str, 'Section', Figure, Table, Equation]]
    
@dataclass
class Figure:
    """Рисунок с caption."""
    figure_number: str    # "1", "2a", etc.
    caption: str          # Подпись (ПЕРЕВОДИТСЯ)
    image_ref: str        # Ссылка на ImageElement
    page_number: int
    position_hint: str    # "top", "bottom", "here"

@dataclass
class Table:
    """Таблица."""
    table_number: str
    caption: str          # ПЕРЕВОДИТСЯ
    content: List[List[str]]  # Ячейки таблицы (ПЕРЕВОДЯТСЯ)
    page_number: int

@dataclass
class Equation:
    """Математическая формула."""
    equation_number: Optional[str]  # "(1)", "(2)", etc.
    latex: str            # LaTeX код формулы (НЕ ПЕРЕВОДИТСЯ)
    is_inline: bool       # Inline или display mode
    page_number: int

@dataclass
class Footnote:
    """Сноска."""
    marker: str           # "1", "*", "†", etc.
    text: str             # ПЕРЕВОДИТСЯ
    page_number: int

@dataclass
class Reference:
    """Элемент списка литературы."""
    number: str           # "[1]", "1.", etc.
    text: str             # НЕ ПЕРЕВОДИТСЯ
```

### 7.3 Типы текстовых блоков (block_type)

```python
BLOCK_TYPES = {
    # Титульная страница
    "document_title": "Заголовок статьи",
    "author_list": "Список авторов",
    "affiliation": "Аффилиация",
    "abstract_label": "Метка 'Abstract'",
    "abstract_text": "Текст аннотации",
    "keywords_label": "Метка 'Keywords'",
    "keywords_text": "Ключевые слова",
    
    # Метаинформация (НЕ ПЕРЕВОДИТСЯ)
    "doi": "DOI",
    "journal_info": "Информация о журнале",
    "dates": "Даты (received, accepted, published)",
    "copyright": "Копирайт",
    "license": "Лицензия",
    "correspondence": "Контактная информация",
    
    # Основной контент
    "section_heading": "Заголовок секции",
    "paragraph": "Обычный параграф",
    "list_item": "Элемент списка",
    "figure_caption": "Подпись к рисунку",
    "table_caption": "Подпись к таблице",
    "equation": "Формула",
    "footnote": "Сноска",
    
    # Список литературы
    "references_heading": "Заголовок 'References'",
    "reference_item": "Элемент списка литературы",
    
    # Специальные
    "acknowledgements": "Благодарности",
    "author_contributions": "Вклад авторов",
    "funding": "Финансирование",
    "conflicts": "Конфликт интересов",
    
    # Header/Footer
    "page_header": "Верхний колонтитул",
    "page_footer": "Нижний колонтитул",
    "page_number": "Номер страницы",
}
```

### 7.4 Алгоритм извлечения

#### 7.4.1 Извлечение с сохранением структуры
```python
def extract_content(doc: fitz.Document, geometry: DocumentGeometry) -> DocumentContent:
    """
    Извлекает весь контент документа с семантической структурой.
    
    Алгоритм (высокий уровень):
    1. Извлечь все текстовые блоки со стилями
    2. Классифицировать каждый блок по типу
    3. Группировать блоки в секции
    4. Извлечь специальные элементы (формулы, таблицы)
    5. Построить структуру документа
    """
    
    # 1. Извлечь все блоки
    all_blocks = []
    for page_num, page in enumerate(doc):
        blocks = extract_page_blocks(page, page_num, geometry)
        all_blocks.extend(blocks)
    
    # 2. Классифицировать блоки
    classified_blocks = classify_blocks(all_blocks, geometry)
    
    # 3. Построить структуру
    content = build_document_structure(classified_blocks)
    
    return content

def extract_page_blocks(page: fitz.Page, page_num: int, geometry: DocumentGeometry) -> List[TextBlock]:
    """
    Извлекает текстовые блоки с одной страницы.
    """
    blocks = []
    
    # Используем "dict" mode для получения полной информации
    page_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
    
    for block in page_dict["blocks"]:
        if block["type"] != 0:  # Только текстовые блоки
            continue
        
        for line in block["lines"]:
            for span in line["spans"]:
                blocks.append(TextBlock(
                    text=span["text"],
                    page_number=page_num,
                    bbox=(span["bbox"][0], span["bbox"][1], 
                          span["bbox"][2], span["bbox"][3]),
                    font_name=span["font"],
                    font_size=span["size"],
                    font_flags=span["flags"],
                    color=color_int_to_hex(span["color"]),
                    block_type="unknown"  # Будет определён позже
                ))
    
    return blocks
```

#### 7.4.2 Классификация блоков
```python
def classify_blocks(blocks: List[TextBlock], geometry: DocumentGeometry) -> List[TextBlock]:
    """
    Классифицирует каждый блок по типу.
    
    Использует эвристики на основе:
    - Позиции на странице
    - Размера шрифта (относительно других блоков)
    - Стиля шрифта (bold, italic)
    - Содержимого текста (keywords, patterns)
    - Контекста (соседние блоки)
    """
    
    # Определяем статистику шрифтов
    font_stats = compute_font_statistics(blocks)
    
    for block in blocks:
        block.block_type = classify_single_block(block, font_stats, geometry)
    
    return blocks

def classify_single_block(block: TextBlock, font_stats: Dict, geometry: DocumentGeometry) -> str:
    """
    Классифицирует один блок.
    """
    text = block.text.strip()
    page_num = block.page_number
    
    # Title page heuristics
    if page_num == 0:
        # Самый большой шрифт на первой странице = заголовок
        if block.font_size == font_stats["max_size"]:
            return "document_title"
        
        # "Abstract" label
        if text.lower() in ["abstract", "summary"]:
            return "abstract_label"
        
        # Keywords
        if text.lower().startswith("keywords"):
            return "keywords_label"
        
        # DOI pattern
        if "doi" in text.lower() or "10.1" in text:
            return "doi"
    
    # References detection
    if text.lower() in ["references", "bibliography", "literature"]:
        return "references_heading"
    
    # Reference item pattern: [1], 1., etc.
    if re.match(r'^\[\d+\]|^\d+\.', text):
        # Нужна дополнительная проверка контекста
        pass
    
    # Section heading: larger font, bold, short text
    if (block.font_size > font_stats["body_size"] * 1.1 and 
        len(text) < 100 and
        block.font_flags & 16):  # bold flag
        return "section_heading"
    
    # Figure caption
    if text.lower().startswith("figure") or text.lower().startswith("fig."):
        return "figure_caption"
    
    # Table caption
    if text.lower().startswith("table"):
        return "table_caption"
    
    # Default: paragraph
    return "paragraph"
```

#### 7.4.3 Извлечение формул
```python
def extract_equations(doc: fitz.Document) -> List[Equation]:
    """
    Извлекает математические формулы.
    
    СЛОЖНОСТЬ: PDF не хранит LaTeX-код формул. 
    Формулы отрендерены как глифы или изображения.
    
    Подходы:
    1. Использовать OCR с math recognition (Mathpix API, pix2tex)
    2. Детектировать formula regions по характерным глифам
    3. Для PDF из LaTeX — формулы могут быть сохранены как аннотации
    
    Для MVP: Извлекать формулы как изображения и вставлять в LaTeX
    с помощью \includegraphics, или использовать Mathpix API.
    """
    equations = []
    
    for page_num, page in enumerate(doc):
        # Детектируем регионы с формулами
        formula_regions = detect_formula_regions(page)
        
        for region in formula_regions:
            # Извлекаем как изображение
            pix = page.get_pixmap(clip=region)
            
            # Опционально: отправить в Mathpix для OCR
            latex_code = mathpix_ocr(pix.tobytes()) if USE_MATHPIX else None
            
            equations.append(Equation(
                equation_number=extract_equation_number(region, page),
                latex=latex_code or f"\\includegraphics{{eq_{page_num}_{len(equations)}.png}}",
                is_inline=region.height < 30,  # threshold
                page_number=page_num
            ))
    
    return equations

def detect_formula_regions(page: fitz.Page) -> List[fitz.Rect]:
    """
    Детектирует регионы с формулами.
    
    Эвристики:
    - Наличие специальных символов (∑, ∫, √, греческие буквы)
    - Вертикальное центрирование текста
    - Unusual font (CMSY, CMMI, Math fonts)
    - Standalone короткий текст с особыми символами
    """
    # Реализация...
    pass
```

### 7.5 Edge Cases для Content Extractor

| Edge Case | Описание | Решение |
|-----------|----------|---------|
| Multi-column abstract | Abstract в двух колонках | Детектировать через геометрию, объединять |
| Footnotes across pages | Сноска переносится на следующую страницу | Отслеживать по маркерам сносок |
| Inline formulas | Формулы внутри текста | Детектировать по шрифтам и символам |
| Subscripts/superscripts | Верхние и нижние индексы | Детектировать по размеру и позиции relative to baseline |
| Ligatures | Лигатуры (fi, fl, etc.) | PyMuPDF обычно обрабатывает автоматически |
| Right-to-left text | Иврит, арабский в references | Сохранять direction |
| Special characters | Символы, отсутствующие в целевом языке | Использовать Unicode, проверять наличие в шрифте |
| Author footnotes | Сноски у авторов (email, contributions) | Классифицировать отдельно |
| Running title | Сокращённый заголовок в header | Не переводить или использовать сокращение перевода |

---

## 8. МОДУЛЬ 4: Font Analyzer

### 8.1 Назначение
Анализ шрифтов документа и подбор подходящих шрифтов для целевого языка.

### 8.2 Структура данных

```python
@dataclass
class FontInfo:
    """Информация о шрифте в документе."""
    name: str              # Имя шрифта в PDF
    family: str            # Семейство (Times, Arial, etc.)
    style: str             # "regular", "bold", "italic", "bolditalic"
    is_serif: bool
    is_monospace: bool
    
    # Использование
    usage_count: int       # Количество символов
    used_for: List[str]    # ["body", "heading", "caption", etc.]
    
    # Размеры (в points)
    sizes_used: List[float]

@dataclass
class FontMapping:
    """Сопоставление оригинального шрифта с шрифтом для перевода."""
    original_font: str
    target_font: str
    scaling_factor: float  # Для компенсации разницы в ширине символов
```

### 8.3 Логика подбора шрифтов

```python
# Шрифты для разных языков
FONT_RECOMMENDATIONS = {
    "ru": {
        "serif": "PT Serif",           # или "Noto Serif"
        "sans": "PT Sans",             # или "Noto Sans"
        "mono": "PT Mono",             # или "Noto Sans Mono"
    },
    "zh": {
        "serif": "Noto Serif CJK SC",
        "sans": "Noto Sans CJK SC",
        "mono": "Noto Sans Mono CJK SC",
    },
    "ja": {
        "serif": "Noto Serif CJK JP",
        "sans": "Noto Sans CJK JP",
        "mono": "Noto Sans Mono CJK JP",
    },
    "ko": {
        "serif": "Noto Serif CJK KR",
        "sans": "Noto Sans CJK KR",
        "mono": "Noto Sans Mono CJK KR",
    },
    "de": {
        "serif": "Libertinus Serif",   # Хорошая поддержка немецких символов
        "sans": "Libertinus Sans",
        "mono": "Inconsolata",
    },
    # ... другие языки
}

def analyze_fonts(doc: fitz.Document) -> List[FontInfo]:
    """
    Анализирует все шрифты в документе.
    """
    font_usage = {}
    
    for page in doc:
        page_dict = page.get_text("dict")
        
        for block in page_dict["blocks"]:
            if block["type"] != 0:
                continue
            
            for line in block["lines"]:
                for span in line["spans"]:
                    font_name = span["font"]
                    font_size = span["size"]
                    text_len = len(span["text"])
                    
                    if font_name not in font_usage:
                        font_usage[font_name] = {
                            "count": 0,
                            "sizes": set(),
                        }
                    
                    font_usage[font_name]["count"] += text_len
                    font_usage[font_name]["sizes"].add(font_size)
    
    # Преобразуем в FontInfo объекты
    fonts = []
    for name, usage in font_usage.items():
        fonts.append(FontInfo(
            name=name,
            family=extract_font_family(name),
            style=extract_font_style(name),
            is_serif=detect_if_serif(name),
            is_monospace=detect_if_mono(name),
            usage_count=usage["count"],
            used_for=determine_font_usage(name, usage),
            sizes_used=list(usage["sizes"])
        ))
    
    return sorted(fonts, key=lambda f: f.usage_count, reverse=True)

def create_font_mapping(fonts: List[FontInfo], target_language: str) -> List[FontMapping]:
    """
    Создаёт сопоставление оригинальных шрифтов с целевыми.
    """
    mappings = []
    recommendations = FONT_RECOMMENDATIONS.get(target_language, FONT_RECOMMENDATIONS["ru"])
    
    for font in fonts:
        if font.is_monospace:
            target = recommendations["mono"]
        elif font.is_serif:
            target = recommendations["serif"]
        else:
            target = recommendations["sans"]
        
        mappings.append(FontMapping(
            original_font=font.name,
            target_font=target,
            scaling_factor=1.0  # Может потребоваться корректировка
        ))
    
    return mappings
```

### 8.4 Edge Cases для Font Analyzer

| Edge Case | Описание | Решение |
|-----------|----------|---------|
| Embedded fonts | Шрифты встроены в PDF | Извлекать информацию из метаданных |
| Subset fonts | Встроены только используемые глифы (ABCDEF+FontName) | Удалять prefix при анализе имени |
| Missing fonts | Шрифт не найден в системе | Fallback на Noto fonts |
| Math fonts | CMSY, CMMI, etc. | Не заменять, использовать оригинальные в формулах |
| Symbol fonts | Wingdings, Symbol | Не заменять |
| CJK in original | Уже есть CJK символы в оригинале | Сохранять для этих символов |

---

## 9. МОДУЛЬ 5: LaTeX Template Generator

### 9.1 Назначение
Генерация LaTeX-шаблона, который воспроизводит геометрию и визуальное оформление оригинального документа.

### 9.2 Структура генерируемого шаблона

```latex
% ==============================================================================
% АВТОМАТИЧЕСКИ СГЕНЕРИРОВАННЫЙ ШАБЛОН
% Документ: {original_filename}
% Дата генерации: {timestamp}
% ==============================================================================

\documentclass[{paper_size}]{article}

% ===== ГЕОМЕТРИЯ СТРАНИЦЫ =====
\usepackage[
    paperwidth={paper_width}pt,
    paperheight={paper_height}pt,
    top={margin_top}pt,
    bottom={margin_bottom}pt,
    left={margin_left}pt,
    right={margin_right}pt,
    headheight={header_height}pt,
    footskip={footer_skip}pt,
]{geometry}

% ===== КОЛОНКИ =====
% Если документ двухколоночный:
\usepackage{multicol}
\setlength{\columnsep}{{column_gap}pt}

% ===== ШРИФТЫ =====
\usepackage{fontspec}  % Требует XeLaTeX или LuaLaTeX
\setmainfont{{main_font}}
\setsansfont{{sans_font}}
\setmonofont{{mono_font}}

% ===== ПОДДЕРЖКА ЯЗЫКОВ =====
\usepackage{polyglossia}
\setmainlanguage{{target_language}}
\setotherlanguage{english}

% ===== АБСОЛЮТНОЕ ПОЗИЦИОНИРОВАНИЕ =====
\usepackage[absolute,overlay]{textpos}
\setlength{\TPHorizModule}{1pt}
\setlength{\TPVertModule}{1pt}

% ===== ИЗОБРАЖЕНИЯ =====
\usepackage{graphicx}
\graphicspath{{{images_folder}}}

% ===== HEADER/FOOTER =====
\usepackage{fancyhdr}
\pagestyle{fancy}
\fancyhf{}
{header_footer_definitions}

% ===== ЛИНИИ И БОКСЫ =====
\usepackage{tikz}

% ===== ФОРМУЛЫ =====
\usepackage{amsmath}
\usepackage{amssymb}

% ===== ТАБЛИЦЫ =====
\usepackage{booktabs}
\usepackage{array}

% ===== ГИПЕРССЫЛКИ =====
\usepackage{hyperref}

% ===== СНОСКИ =====
\usepackage[bottom]{footmisc}  % Сноски внизу страницы

% ==============================================================================
% КАСТОМНЫЕ КОМАНДЫ ДЛЯ ЭЛЕМЕНТОВ ДОКУМЕНТА
% ==============================================================================

% Команда для абсолютного позиционирования элементов
\newcommand{\absposition}[4]{%
    \begin{textblock*}{#3}(#1,#2)
        #4
    \end{textblock*}
}

% Горизонтальная линия (title underline, section separator, etc.)
\newcommand{\hlineabsolute}[5]{%
    % #1 = x, #2 = y, #3 = length, #4 = thickness, #5 = color
    \begin{tikzpicture}[remember picture, overlay]
        \draw[line width=#4, color=#5] (#1, -#2) -- ++(#3, 0);
    \end{tikzpicture}
}

% ==============================================================================
% НАЧАЛО ДОКУМЕНТА
% ==============================================================================

\begin{document}

% ===== ТИТУЛЬНАЯ СТРАНИЦА =====
\thispagestyle{empty}  % Или специальный стиль

% Абсолютно позиционированные элементы (логотипы, линии, боксы)
{{title_page_absolute_elements}}

% Заголовок статьи
{{title_block}}

% Авторы и аффилиации
{{authors_block}}

% Abstract
{{abstract_block}}

% Keywords
{{keywords_block}}

% ===== ОСНОВНОЙ КОНТЕНТ =====
{{main_content}}

% ===== REFERENCES (НЕ ПЕРЕВОДИТСЯ) =====
{{references_section}}

\end{document}
```

### 9.3 Генерация компонентов шаблона

#### 9.3.1 Генерация геометрии
```python
def generate_geometry_preamble(geometry: DocumentGeometry) -> str:
    """
    Генерирует LaTeX-код для настройки геометрии страницы.
    """
    # Используем геометрию основных страниц (не титульной)
    default_geom = next(
        (p for p in geometry.pages if not p.is_title_page),
        geometry.pages[0]
    )
    
    return f"""\\usepackage[
    paperwidth={geometry.paper_width_pt}pt,
    paperheight={geometry.paper_height_pt}pt,
    top={default_geom.margin_top_pt}pt,
    bottom={default_geom.margin_bottom_pt}pt,
    left={default_geom.margin_left_pt}pt,
    right={default_geom.margin_right_pt}pt,
    headheight={default_geom.header_height_pt}pt,
    footskip={default_geom.footer_height_pt + 10}pt,
]{{geometry}}"""
```

#### 9.3.2 Генерация абсолютных элементов
```python
def generate_absolute_elements(
    elements: List[Union[ImageElement, LineElement, BoxElement]],
    page_number: int
) -> str:
    """
    Генерирует LaTeX-код для абсолютно позиционированных элементов.
    """
    latex_parts = []
    
    for elem in elements:
        if elem.page_number != page_number:
            continue
        
        if isinstance(elem, ImageElement) and elem.positioning == "absolute":
            latex_parts.append(f"""\\absposition{{{elem.x}pt}}{{{elem.y}pt}}{{{elem.width}pt}}{{%
    \\includegraphics[width={elem.width}pt]{{images/{elem.image_ref}}}
}}""")
        
        elif isinstance(elem, LineElement):
            latex_parts.append(f"""\\hlineabsolute{{{elem.x1}pt}}{{{elem.y1}pt}}{{{elem.x2 - elem.x1}pt}}{{{elem.stroke_width}pt}}{{{elem.stroke_color}}}""")
        
        elif isinstance(elem, BoxElement):
            # Генерация tikz прямоугольника
            latex_parts.append(generate_tikz_box(elem))
    
    return "\n".join(latex_parts)
```

#### 9.3.3 Генерация header/footer
```python
def generate_header_footer_setup(headers_footers: List[HeaderFooterElement]) -> str:
    """
    Генерирует настройки fancyhdr для header и footer.
    """
    header_left = ""
    header_center = ""
    header_right = ""
    footer_left = ""
    footer_center = ""
    footer_right = ""
    
    for elem in headers_footers:
        content = elem.content.replace("{{page_number}}", "\\thepage")
        
        if elem.position == "header":
            if elem.alignment == "left":
                header_left = content
            elif elem.alignment == "center":
                header_center = content
            else:
                header_right = content
        else:  # footer
            if elem.alignment == "left":
                footer_left = content
            elif elem.alignment == "center":
                footer_center = content
            else:
                footer_right = content
    
    return f"""\\fancyhead[L]{{{header_left}}}
\\fancyhead[C]{{{header_center}}}
\\fancyhead[R]{{{header_right}}}
\\fancyfoot[L]{{{footer_left}}}
\\fancyfoot[C]{{{footer_center}}}
\\fancyfoot[R]{{{footer_right}}}
\\renewcommand{{\\headrulewidth}}{{0pt}}
\\renewcommand{{\\footrulewidth}}{{0pt}}"""
```

### 9.4 Специальная обработка титульной страницы

```python
def generate_title_page(
    content: DocumentContent,
    elements: List[Union[ImageElement, LineElement, BoxElement]],
    geometry: PageGeometry
) -> str:
    """
    Генерирует LaTeX-код для титульной страницы.
    
    Титульная страница обычно имеет особую структуру:
    - Логотипы журнала (absolute positioning)
    - Название статьи (центрировано или с особым форматированием)
    - Линии-разделители
    - Боковые панели с метаинформацией (как в Frontiers)
    - Авторы с разными стилями (footnote markers, affiliations)
    """
    
    parts = []
    
    # 1. Абсолютные элементы (логотипы, линии, боксы)
    parts.append("% ===== АБСОЛЮТНЫЕ ЭЛЕМЕНТЫ ТИТУЛЬНОЙ СТРАНИЦЫ =====")
    parts.append(generate_absolute_elements(elements, page_number=0))
    
    # 2. Название статьи
    parts.append("% ===== ЗАГОЛОВОК =====")
    parts.append(generate_title_block(content.title, geometry))
    
    # 3. Авторы
    parts.append("% ===== АВТОРЫ =====")
    parts.append(generate_authors_block(content.authors, content.affiliations))
    
    # 4. Abstract
    parts.append("% ===== АННОТАЦИЯ =====")
    parts.append(generate_abstract_block(content.abstract))
    
    # 5. Keywords
    if content.keywords:
        parts.append("% ===== КЛЮЧЕВЫЕ СЛОВА =====")
        parts.append(generate_keywords_block(content.keywords))
    
    return "\n\n".join(parts)

def generate_title_block(title: str, geometry: PageGeometry) -> str:
    """
    Генерирует блок заголовка.
    
    ПРИМЕЧАНИЕ: Здесь {{TITLE}} - placeholder для переведённого заголовка.
    """
    return f"""{{\\centering
    {{\\fontsize{{16pt}}{{18pt}}\\selectfont\\bfseries 
    {{{{TITLE}}}}
    }}\\par
}}
\\vspace{{12pt}}"""

def generate_authors_block(authors: List[AuthorInfo], affiliations: List[str]) -> str:
    """
    Генерирует блок авторов с аффилиациями.
    
    Разные журналы используют разные форматы:
    - Авторы через запятую с номерными сносками
    - Авторы с email в сносках
    - Авторы с символьными маркерами (*, †, ‡)
    """
    # Simplified version - в реальности нужно детектировать формат
    author_names = ", ".join(a.name for a in authors)
    affiliations_text = " \\\\ ".join(affiliations)
    
    return f"""{{\\centering
    {author_names}
    \\vspace{{6pt}}
    
    {{\\small\\itshape {affiliations_text}}}
    \\par
}}
\\vspace{{12pt}}"""
```

### 9.5 Edge Cases для Template Generator

| Edge Case | Описание | Решение |
|-----------|----------|---------|
| Разная геометрия для чётных/нечётных страниц | Зеркальные margins | Использовать `twoside` option |
| Титульная без номера | Первая страница без нумерации | `\thispagestyle{empty}` |
| Multi-column abstract | Abstract на всю ширину, затем две колонки | `\begin{multicols}` после abstract |
| Floating figures | Figures могут смещаться | Использовать `[h!]` или `[H]` (float package) |
| Wide tables | Таблицы шире колонки | `\begin{table*}` для spanning columns |
| Landscape pages | Страницы в альбомной ориентации | `lscape` package |
| Color text | Цветной текст (highlights, etc.) | `xcolor` package |

---

## 10. МОДУЛЬ 6: Translator

### 10.1 Назначение
Перевод текстового контента с сохранением структуры и специальных элементов.

### 10.2 Правила перевода

```python
TRANSLATION_RULES = {
    # ПЕРЕВОДИТСЯ
    "document_title": True,
    "abstract_text": True,
    "keywords_text": True,
    "section_heading": True,
    "paragraph": True,
    "list_item": True,
    "figure_caption": True,
    "table_caption": True,
    "table_cell": True,      # Содержимое ячеек таблиц
    "footnote": True,
    "acknowledgements": True,
    
    # НЕ ПЕРЕВОДИТСЯ
    "doi": False,
    "journal_info": False,
    "dates": False,
    "copyright": False,
    "license": False,
    "correspondence": False,  # Email и контакты
    "reference_item": False,  # Список литературы
    "equation": False,        # Формулы
    "author_name": False,     # Имена авторов
    "affiliation": False,     # Названия организаций (опционально)
    "page_header": False,
    "page_footer": False,
    
    # ОПЦИОНАЛЬНО (настраивается пользователем)
    "abstract_label": "optional",  # "Abstract" → "Аннотация"
    "keywords_label": "optional",  # "Keywords" → "Ключевые слова"
    "references_heading": "optional",  # "References" → "Список литературы"
}
```

### 10.3 Алгоритм перевода

```python
def translate_content(
    content: DocumentContent,
    source_lang: str,
    target_lang: str,
    api_client: TranslationAPI
) -> DocumentContent:
    """
    Переводит контент документа.
    
    ВАЖНО:
    1. Сохранять inline formatting (bold, italic)
    2. Сохранять ссылки на цитаты [1], [2], etc.
    3. Сохранять ссылки на формулы (Eq. 1), (1)
    4. Сохранять ссылки на рисунки (Fig. 1), (Figure 1)
    5. Не переводить термины в кавычках (опционально)
    6. Использовать контекст для disambiguation
    """
    
    translated = content.copy()
    
    # Перевод заголовка
    translated.title = translate_text(content.title, source_lang, target_lang, api_client)
    
    # Перевод abstract
    translated.abstract = translate_text(content.abstract, source_lang, target_lang, api_client)
    
    # Перевод keywords
    translated.keywords = [
        translate_text(kw, source_lang, target_lang, api_client)
        for kw in content.keywords
    ]
    
    # Перевод секций
    translated.sections = translate_sections(content.sections, source_lang, target_lang, api_client)
    
    # Перевод captions
    translated.figures = [
        Figure(
            figure_number=fig.figure_number,
            caption=translate_text(fig.caption, source_lang, target_lang, api_client),
            image_ref=fig.image_ref,
            page_number=fig.page_number,
            position_hint=fig.position_hint
        )
        for fig in content.figures
    ]
    
    # ... аналогично для tables, footnotes
    
    return translated

def translate_text(
    text: str,
    source_lang: str,
    target_lang: str,
    api_client: TranslationAPI,
    preserve_patterns: List[str] = None
) -> str:
    """
    Переводит текст с сохранением специальных паттернов.
    """
    if not preserve_patterns:
        preserve_patterns = [
            r'\[[\d,\s-]+\]',          # Citations [1], [1-3], [1, 2, 3]
            r'\(Eq\.\s*\d+\)',          # Equation references
            r'\(Figure\s*\d+\)',        # Figure references
            r'\(Fig\.\s*\d+[a-z]?\)',   # Fig. references
            r'\(Table\s*\d+\)',         # Table references
            r'\$[^$]+\$',               # Inline math
        ]
    
    # Заменяем паттерны на placeholders
    placeholders = {}
    processed_text = text
    
    for i, pattern in enumerate(preserve_patterns):
        for match in re.finditer(pattern, processed_text):
            placeholder = f"__PLACEHOLDER_{i}_{len(placeholders)}__"
            placeholders[placeholder] = match.group()
            processed_text = processed_text.replace(match.group(), placeholder, 1)
    
    # Переводим
    translated = api_client.translate(processed_text, source_lang, target_lang)
    
    # Восстанавливаем placeholders
    for placeholder, original in placeholders.items():
        translated = translated.replace(placeholder, original)
    
    return translated
```

### 10.4 Edge Cases для Translator

| Edge Case | Описание | Решение |
|-----------|----------|---------|
| Abbreviations | Аббревиатуры (DNA, RNA, PCR) | Не переводить общепринятые научные аббревиатуры |
| Technical terms | Специфические термины | Использовать glossary/terminology database |
| Hyphenated words | Переносы слов на конце строки | Объединять перед переводом |
| Multi-paragraph text | Длинные тексты | Разбивать на абзацы, переводить с контекстом |
| Nested references | "as shown in Fig. 1 and Table 2" | Сохранять все ссылки |
| Quoted text | Текст в кавычках | Опционально не переводить |
| Code snippets | Примеры кода | Не переводить |
| URLs | Ссылки в тексте | Не переводить |
| Expansion for Russian | Русский текст на 15-30% длиннее | LaTeX справится с reflow |
| Contraction for CJK | Китайский текст короче | Аналогично |

---

## 11. МОДУЛЬ 7: LaTeX Assembler

### 11.1 Назначение
Сборка финального LaTeX-документа из шаблона и переведённого контента.

### 11.2 Процесс сборки

```python
def assemble_latex_document(
    template: str,
    translated_content: DocumentContent,
    elements: Dict,
    images_folder: str
) -> str:
    """
    Собирает финальный LaTeX-документ.
    
    1. Подставляет переведённый контент в placeholders
    2. Генерирует секции
    3. Вставляет figures, tables, equations
    4. Добавляет references (без изменений)
    """
    
    document = template
    
    # Подстановка заголовка
    document = document.replace("{{TITLE}}", escape_latex(translated_content.title))
    
    # Подстановка abstract
    document = document.replace("{{ABSTRACT}}", escape_latex(translated_content.abstract))
    
    # Генерация основного контента
    main_content = generate_main_content(
        translated_content.sections,
        translated_content.figures,
        translated_content.tables,
        translated_content.equations,
        translated_content.footnotes
    )
    document = document.replace("{{main_content}}", main_content)
    
    # Генерация references
    references = generate_references_section(translated_content.references)
    document = document.replace("{{references_section}}", references)
    
    return document

def generate_main_content(
    sections: List[Section],
    figures: List[Figure],
    tables: List[Table],
    equations: List[Equation],
    footnotes: List[Footnote]
) -> str:
    """
    Генерирует LaTeX-код для основного контента.
    """
    latex_parts = []
    
    for section in sections:
        # Заголовок секции
        section_cmd = {
            1: "\\section",
            2: "\\subsection",
            3: "\\subsubsection",
        }.get(section.level, "\\paragraph")
        
        latex_parts.append(f"{section_cmd}{{{escape_latex(section.title)}}}")
        
        # Контент секции
        for item in section.content:
            if isinstance(item, str):
                # Обычный параграф
                latex_parts.append(escape_latex(item))
                latex_parts.append("")  # Пустая строка между параграфами
            
            elif isinstance(item, Figure):
                latex_parts.append(generate_figure_latex(item))
            
            elif isinstance(item, Table):
                latex_parts.append(generate_table_latex(item))
            
            elif isinstance(item, Equation):
                latex_parts.append(generate_equation_latex(item))
            
            elif isinstance(item, Section):
                # Вложенная секция
                latex_parts.append(generate_main_content(
                    [item], [], [], [], []
                ))
    
    return "\n\n".join(latex_parts)

def generate_figure_latex(figure: Figure) -> str:
    """
    Генерирует LaTeX-код для рисунка.
    """
    position = "htbp"  # Default positioning
    
    return f"""\\begin{{figure}}[{position}]
    \\centering
    \\includegraphics[width=0.9\\columnwidth]{{images/{figure.image_ref}}}
    \\caption{{{escape_latex(figure.caption)}}}
    \\label{{fig:{figure.figure_number}}}
\\end{{figure}}"""

def generate_table_latex(table: Table) -> str:
    """
    Генерирует LaTeX-код для таблицы.
    """
    # Определяем количество колонок
    num_cols = len(table.content[0]) if table.content else 0
    col_spec = "l" * num_cols  # Простая спецификация
    
    rows = []
    for row in table.content:
        cells = " & ".join(escape_latex(cell) for cell in row)
        rows.append(cells + " \\\\")
    
    return f"""\\begin{{table}}[htbp]
    \\centering
    \\caption{{{escape_latex(table.caption)}}}
    \\label{{tab:{table.table_number}}}
    \\begin{{tabular}}{{{col_spec}}}
        \\toprule
        {rows[0]}
        \\midrule
        {chr(10).join(rows[1:])}
        \\bottomrule
    \\end{{tabular}}
\\end{{table}}"""

def escape_latex(text: str) -> str:
    """
    Экранирует специальные символы LaTeX.
    """
    replacements = [
        ('\\', '\\textbackslash{}'),
        ('&', '\\&'),
        ('%', '\\%'),
        ('$', '\\$'),
        ('#', '\\#'),
        ('_', '\\_'),
        ('{', '\\{'),
        ('}', '\\}'),
        ('~', '\\textasciitilde{}'),
        ('^', '\\textasciicircum{}'),
    ]
    
    for old, new in replacements:
        text = text.replace(old, new)
    
    return text
```

### 11.3 Edge Cases для Assembler

| Edge Case | Описание | Решение |
|-----------|----------|---------|
| Special characters | €, ©, ®, etc. | Использовать Unicode с XeLaTeX |
| Long URLs | URLs, которые не помещаются в строку | `\url{}` с `breaklinks` |
| Very long words | Длинные слова без возможности переноса | `\hyphenation{}` или `\allowbreak` |
| Empty sections | Секции без контента | Пропускать или добавлять placeholder |
| Nested lists | Вложенные списки | Поддержка до 4 уровней |
| Cross-references | Ссылки внутри документа | Использовать `\ref{}` |

---

## 12. МОДУЛЬ 8: PDF Compiler

### 12.1 Назначение
Компиляция LaTeX-документа в PDF.

### 12.2 Требования

- Использовать **XeLaTeX** или **LuaLaTeX** для поддержки Unicode и OpenType fonts
- Несколько проходов компиляции для правильных cross-references
- Обработка ошибок компиляции

### 12.3 Реализация

```python
import subprocess
import tempfile
import shutil
from pathlib import Path

def compile_latex_to_pdf(
    latex_content: str,
    images_folder: Path,
    output_path: Path,
    engine: str = "xelatex"
) -> Tuple[bool, str]:
    """
    Компилирует LaTeX в PDF.
    
    Returns:
        (success: bool, error_message: str)
    """
    
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        
        # Сохраняем LaTeX файл
        tex_file = temp_path / "document.tex"
        tex_file.write_text(latex_content, encoding="utf-8")
        
        # Копируем изображения
        images_dest = temp_path / "images"
        shutil.copytree(images_folder, images_dest)
        
        # Компиляция (два прохода для cross-references)
        for pass_num in range(2):
            result = subprocess.run(
                [
                    engine,
                    "-interaction=nonstopmode",
                    "-halt-on-error",
                    "-output-directory", str(temp_path),
                    str(tex_file)
                ],
                capture_output=True,
                text=True,
                cwd=temp_path
            )
            
            if result.returncode != 0:
                # Извлекаем ошибку из лога
                log_file = temp_path / "document.log"
                error = extract_latex_error(log_file)
                return False, error
        
        # Копируем результат
        pdf_file = temp_path / "document.pdf"
        if pdf_file.exists():
            shutil.copy(pdf_file, output_path)
            return True, ""
        else:
            return False, "PDF file was not generated"

def extract_latex_error(log_file: Path) -> str:
    """
    Извлекает понятное сообщение об ошибке из лога LaTeX.
    """
    if not log_file.exists():
        return "Log file not found"
    
    log_content = log_file.read_text(encoding="utf-8", errors="replace")
    
    # Ищем строки с ошибками
    error_lines = []
    for line in log_content.split("\n"):
        if line.startswith("!") or "Error:" in line:
            error_lines.append(line)
    
    return "\n".join(error_lines[:10])  # Первые 10 ошибок
```

### 12.4 Edge Cases для Compiler

| Edge Case | Описание | Решение |
|-----------|----------|---------|
| Missing fonts | Шрифт не найден | Fallback на системные шрифты, предупреждение |
| Missing packages | LaTeX package не установлен | Использовать базовые packages, инструкция по установке |
| Out of memory | Очень большой документ | Увеличить memory limits или разбить документ |
| Infinite loop | Ошибка в макросах | Timeout на компиляцию |
| Unicode errors | Символы не поддерживаются шрифтом | Fallback fonts |

---

## 13. Структуры данных (Data Models)

### 13.1 Полная структура документа

```python
# models/document.py

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple, Union
from enum import Enum

class BlockType(Enum):
    DOCUMENT_TITLE = "document_title"
    AUTHOR_LIST = "author_list"
    AFFILIATION = "affiliation"
    ABSTRACT_LABEL = "abstract_label"
    ABSTRACT_TEXT = "abstract_text"
    KEYWORDS_LABEL = "keywords_label"
    KEYWORDS_TEXT = "keywords_text"
    DOI = "doi"
    JOURNAL_INFO = "journal_info"
    DATES = "dates"
    COPYRIGHT = "copyright"
    LICENSE = "license"
    CORRESPONDENCE = "correspondence"
    SECTION_HEADING = "section_heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    FIGURE_CAPTION = "figure_caption"
    TABLE_CAPTION = "table_caption"
    EQUATION = "equation"
    FOOTNOTE = "footnote"
    REFERENCES_HEADING = "references_heading"
    REFERENCE_ITEM = "reference_item"
    ACKNOWLEDGEMENTS = "acknowledgements"
    AUTHOR_CONTRIBUTIONS = "author_contributions"
    FUNDING = "funding"
    CONFLICTS = "conflicts"
    PAGE_HEADER = "page_header"
    PAGE_FOOTER = "page_footer"
    PAGE_NUMBER = "page_number"
    UNKNOWN = "unknown"

@dataclass
class BoundingBox:
    x0: float
    y0: float
    x1: float
    y1: float
    
    @property
    def width(self) -> float:
        return self.x1 - self.x0
    
    @property
    def height(self) -> float:
        return self.y1 - self.y0

@dataclass
class TextStyle:
    font_name: str
    font_size: float
    is_bold: bool
    is_italic: bool
    color: str  # hex

@dataclass
class TextBlock:
    text: str
    page_number: int
    bbox: BoundingBox
    style: TextStyle
    block_type: BlockType
    
@dataclass
class AuthorInfo:
    name: str
    affiliations: List[int]  # Индексы аффилиаций
    email: Optional[str] = None
    orcid: Optional[str] = None
    is_corresponding: bool = False

@dataclass
class Section:
    level: int  # 1, 2, 3, etc.
    title: str
    content: List[Union[str, 'Section']] = field(default_factory=list)
    
@dataclass
class Figure:
    number: str
    caption: str
    image_path: str
    page_number: int
    bbox: BoundingBox
    
@dataclass
class Table:
    number: str
    caption: str
    headers: List[str]
    rows: List[List[str]]
    page_number: int
    
@dataclass
class Equation:
    number: Optional[str]
    latex: str
    is_inline: bool
    image_path: Optional[str]  # Если нет LaTeX, используем изображение
    
@dataclass
class Footnote:
    marker: str
    text: str
    page_number: int
    
@dataclass
class Reference:
    number: str
    text: str  # Полный текст ссылки (не переводится)

@dataclass
class DocumentContent:
    # Метаданные
    title: str
    authors: List[AuthorInfo]
    affiliations: List[str]
    abstract: str
    keywords: List[str]
    
    # Не переводимые метаданные
    doi: Optional[str] = None
    journal_name: Optional[str] = None
    received_date: Optional[str] = None
    accepted_date: Optional[str] = None
    published_date: Optional[str] = None
    copyright_text: Optional[str] = None
    license_text: Optional[str] = None
    
    # Основной контент
    sections: List[Section] = field(default_factory=list)
    
    # Плавающие элементы
    figures: List[Figure] = field(default_factory=list)
    tables: List[Table] = field(default_factory=list)
    equations: List[Equation] = field(default_factory=list)
    footnotes: List[Footnote] = field(default_factory=list)
    
    # References (не переводится)
    references: List[Reference] = field(default_factory=list)
    
    # Дополнительные секции
    acknowledgements: Optional[str] = None
    author_contributions: Optional[str] = None
    funding: Optional[str] = None
    conflicts: Optional[str] = None
```

### 13.2 Геометрия документа

```python
# models/geometry.py

@dataclass
class PageGeometry:
    page_number: int
    width_pt: float
    height_pt: float
    
    margin_top_pt: float
    margin_bottom_pt: float
    margin_left_pt: float
    margin_right_pt: float
    
    num_columns: int
    column_width_pt: float
    column_gap_pt: float
    
    header_height_pt: float
    footer_height_pt: float
    
    is_title_page: bool = False
    
@dataclass
class DocumentGeometry:
    pages: List[PageGeometry]
    paper_format: str  # "a4", "letter", "custom"
    paper_width_pt: float
    paper_height_pt: float
    
    # Преобладающие значения
    default_num_columns: int = 1
    default_margins: Dict[str, float] = field(default_factory=dict)
```

### 13.3 Визуальные элементы

```python
# models/elements.py

@dataclass
class ImageElement:
    page_number: int
    bbox: BoundingBox
    image_data: bytes
    image_format: str
    dpi: int
    element_type: str  # "figure", "logo", "icon", "decorative"
    positioning: str  # "absolute", "float"
    extracted_path: Optional[str] = None  # Путь к сохранённому файлу

@dataclass
class LineElement:
    page_number: int
    x1: float
    y1: float
    x2: float
    y2: float
    stroke_width: float
    stroke_color: str
    line_type: str  # "horizontal", "vertical", "diagonal"
    semantic_role: str  # "title_underline", "section_separator", etc.

@dataclass
class BoxElement:
    page_number: int
    bbox: BoundingBox
    stroke_width: float
    stroke_color: str
    fill_color: Optional[str]
    semantic_role: str  # "abstract_box", "warning_box", etc.

@dataclass
class HeaderFooterElement:
    page_number: int
    position: str  # "header", "footer"
    content: str
    bbox: BoundingBox
    style: TextStyle
    alignment: str  # "left", "center", "right"
    has_page_number: bool = False

@dataclass
class ExtractedElements:
    images: List[ImageElement]
    lines: List[LineElement]
    boxes: List[BoxElement]
    headers_footers: List[HeaderFooterElement]
```

---

## 14. Примеры обработки различных типов документов

### 14.1 Пример 1: "Attention Is All You Need" (arXiv style)

**Характеристики:**
- Формат: Letter (8.5" × 11")
- Layout: Single-column для title page, затем single-column
- Margins: Большие (≈1.5" по бокам)
- Header: Отсутствует на первой странице, затем минимальный
- Footer: Номера страниц
- Шрифты: Computer Modern (LaTeX default)
- Особенности: Footnotes для авторов с affiliations

**Извлекаемые элементы:**
```python
# Титульная страница
title_page_elements = {
    "absolute": [],  # Нет логотипов
    "title": {
        "text": "Attention Is All You Need",
        "style": {"font_size": 17, "bold": True},
        "position": "center"
    },
    "authors": {
        "format": "grid_with_footnotes",  # Авторы в таблице с footnotes
        "footnotes": ["*", "†", "‡"]
    },
    "lines": [],  # Нет декоративных линий
}

# Geometry
geometry = {
    "paper": "letter",
    "margins": {"top": 108, "bottom": 108, "left": 108, "right": 108},  # ~1.5"
    "columns": 1,
    "header": 0,
    "footer": 36  # Только номер страницы
}
```

### 14.2 Пример 2: "Grand challenges in chemical biology" (Frontiers style)

**Характеристики:**
- Формат: A4
- Layout: Two-column для основного текста
- Особая титульная страница:
  - Левая боковая панель с метаинформацией
  - Логотип Frontiers сверху
  - Специальные badges/labels ("SPECIALTY GRAND CHALLENGE")
  - Информационные блоки (EDITED BY, CITATION, COPYRIGHT)
- Header: Логотип + название журнала + номера страниц
- Footer: frontiersin.org
- Цветные элементы (синие заголовки, оранжевые линии)

**Извлекаемые элементы:**
```python
# Титульная страница
title_page_elements = {
    "absolute": [
        {
            "type": "logo",
            "position": {"x": 36, "y": 20},
            "image": "frontiers_logo.png"
        },
        {
            "type": "sidebar",
            "position": {"x": 36, "y": 100},
            "width": 150,
            "content": [
                {"type": "label", "text": "SPECIALTY GRAND CHALLENGE"},
                {"type": "label", "text": "PUBLISHED 24 November 2025"},
                {"type": "doi", "text": "DOI 10.3389/fchbi.2025.1731631"}
            ]
        },
        {
            "type": "info_boxes",
            "position": {"x": 36, "y": 600},
            "boxes": [
                {"label": "EDITED AND REVIEWED BY", "content": "..."},
                {"label": "CORRESPONDENCE", "content": "..."},
                {"label": "RECEIVED", "content": "24 October 2025"},
                # ...
            ]
        }
    ],
    "title": {
        "text": "Grand challenges in chemical biology...",
        "style": {"font_size": 14, "bold": True, "color": "#1a1a1a"},
        "position": {"x": 200, "y": 150}  # Справа от sidebar
    },
    "lines": [
        {
            "type": "horizontal",
            "position": {"y": 750},
            "color": "#f5a623"  # Оранжевая линия
        }
    ]
}

# Geometry
geometry = {
    "paper": "a4",
    "title_page_margins": {"top": 72, "bottom": 50, "left": 200, "right": 36},
    "body_margins": {"top": 72, "bottom": 50, "left": 36, "right": 36},
    "columns": 2,
    "column_gap": 20,
    "header": {"height": 40, "content": "Frontiers in Chemical Biology | {{page_number}}"},
    "footer": {"height": 30, "content": "frontiersin.org"}
}
```

---

## 15. Конфигурация и настройки

### 15.1 Файл конфигурации

```yaml
# config.yaml

# Настройки перевода
translation:
  source_language: "en"
  target_language: "ru"
  
  # API provider (можно добавить другие)
  provider: "anthropic"  # или "openai", "google", etc.
  api_key_env: "ANTHROPIC_API_KEY"  # Имя переменной окружения
  
  # Что переводить
  translate_labels: true  # "Abstract" → "Аннотация"
  preserve_abbreviations: true  # Не переводить DNA, RNA, etc.
  
# Настройки шрифтов
fonts:
  ru:
    main: "PT Serif"
    sans: "PT Sans"
    mono: "PT Mono"
  zh:
    main: "Noto Serif CJK SC"
    sans: "Noto Sans CJK SC"

# Настройки компиляции
compilation:
  engine: "xelatex"  # или "lualatex"
  passes: 2
  timeout_seconds: 120

# Настройки обработки
processing:
  extract_equations_as_images: true
  use_mathpix_for_equations: false  # Если true, требуется MATHPIX_API_KEY
  preserve_original_fonts: false  # Пытаться использовать оригинальные шрифты
  
# Пути
paths:
  temp_folder: "/tmp/pdf_translator"
  output_folder: "./output"
  fonts_folder: "./assets/fonts"
```

### 15.2 CLI Interface

```python
# main.py

import argparse

def main():
    parser = argparse.ArgumentParser(
        description="Translate PDF scientific papers while preserving layout"
    )
    
    parser.add_argument(
        "input_pdf",
        help="Path to input PDF file"
    )
    
    parser.add_argument(
        "-o", "--output",
        help="Path to output PDF file",
        default=None
    )
    
    parser.add_argument(
        "-l", "--language",
        help="Target language code (ru, zh, ja, ko, de, fr, es)",
        default="ru"
    )
    
    parser.add_argument(
        "-c", "--config",
        help="Path to config file",
        default="config.yaml"
    )
    
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Save intermediate files for debugging"
    )
    
    args = parser.parse_args()
    
    # Запуск pipeline
    from pipeline import TranslationPipeline
    
    pipeline = TranslationPipeline(config_path=args.config)
    
    output_path = args.output or args.input_pdf.replace(".pdf", f"_{args.language}.pdf")
    
    result = pipeline.process(
        input_pdf=args.input_pdf,
        output_pdf=output_path,
        target_language=args.language,
        debug=args.debug
    )
    
    if result.success:
        print(f"Success! Translated PDF saved to: {output_path}")
    else:
        print(f"Error: {result.error_message}")
        exit(1)

if __name__ == "__main__":
    main()
```

---

## 16. Тестирование

### 16.1 Unit Tests

```python
# tests/test_geometry_extractor.py

import pytest
from src.extractors.geometry_extractor import GeometryExtractor

class TestGeometryExtractor:
    
    def test_detect_page_size_a4(self):
        """Тест определения размера A4."""
        # A4: 595 × 842 points
        extractor = GeometryExtractor()
        result = extractor.detect_paper_format(595, 842)
        assert result == "a4"
    
    def test_detect_page_size_letter(self):
        """Тест определения размера Letter."""
        # Letter: 612 × 792 points
        extractor = GeometryExtractor()
        result = extractor.detect_paper_format(612, 792)
        assert result == "letter"
    
    def test_detect_margins(self):
        """Тест определения margins."""
        # Mock page с известными text blocks
        pass
    
    def test_detect_two_column_layout(self):
        """Тест определения двухколоночного layout."""
        pass
    
    def test_detect_single_column_layout(self):
        """Тест определения одноколоночного layout."""
        pass
```

### 16.2 Integration Tests

```python
# tests/test_pipeline.py

import pytest
from pathlib import Path
from src.pipeline import TranslationPipeline

class TestPipeline:
    
    @pytest.fixture
    def sample_arxiv_pdf(self):
        return Path("tests/fixtures/arxiv_sample.pdf")
    
    @pytest.fixture
    def sample_frontiers_pdf(self):
        return Path("tests/fixtures/frontiers_sample.pdf")
    
    def test_full_pipeline_arxiv_style(self, sample_arxiv_pdf, tmp_path):
        """Интеграционный тест для arXiv-style документа."""
        pipeline = TranslationPipeline()
        output_path = tmp_path / "output.pdf"
        
        result = pipeline.process(
            input_pdf=str(sample_arxiv_pdf),
            output_pdf=str(output_path),
            target_language="ru"
        )
        
        assert result.success
        assert output_path.exists()
    
    def test_full_pipeline_frontiers_style(self, sample_frontiers_pdf, tmp_path):
        """Интеграционный тест для Frontiers-style документа."""
        pipeline = TranslationPipeline()
        output_path = tmp_path / "output.pdf"
        
        result = pipeline.process(
            input_pdf=str(sample_frontiers_pdf),
            output_pdf=str(output_path),
            target_language="ru"
        )
        
        assert result.success
        assert output_path.exists()
```

---

## 17. Зависимости

### 17.1 requirements.txt

```
# PDF processing
PyMuPDF>=1.23.0

# Layout Detection (DocLayout-YOLO)
doclayout-yolo>=0.0.2
ultralytics>=8.0.0
torch>=2.0.0
torchvision>=0.15.0

# Image processing
Pillow>=10.0.0
numpy>=1.24.0
opencv-python>=4.8.0  # Для pre-processing изображений

# LaTeX compilation (system dependency: texlive)
# Нет Python package, но нужен xelatex в PATH

# Translation API
anthropic>=0.25.0  # Или другой API client

# Data processing
pyyaml>=6.0
dataclasses-json>=0.6.0

# CLI
click>=8.0.0

# Testing
pytest>=7.0.0
pytest-cov>=4.0.0

# Type checking (optional)
mypy>=1.0.0

# Formatting (optional)
black>=23.0.0
isort>=5.0.0
```

### 17.2 Системные зависимости

```bash
# Ubuntu/Debian
apt-get install -y \
    texlive-xetex \
    texlive-fonts-recommended \
    texlive-fonts-extra \
    texlive-latex-extra \
    texlive-lang-cyrillic \    # Для русского
    texlive-lang-cjk \         # Для китайского/японского/корейского
    fonts-noto \               # Noto fonts
    fonts-liberation           # Liberation fonts (Times/Arial alternatives)

# macOS (с Homebrew)
brew install --cask mactex
brew tap homebrew/cask-fonts
brew install --cask font-noto-sans font-noto-serif
```

### 17.3 GPU (опционально, но рекомендуется для DocLayout-YOLO)

DocLayout-YOLO работает значительно быстрее с GPU:
- **С GPU**: ~0.1-0.3 сек/страница
- **Без GPU (CPU)**: ~1-3 сек/страница

```bash
# Проверка доступности CUDA
python -c "import torch; print(f'CUDA available: {torch.cuda.is_available()}')"

# Установка PyTorch с CUDA (если есть NVIDIA GPU)
# CUDA 11.8
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu118

# CUDA 12.1
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121

# Только CPU (без GPU)
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
```

### 17.4 Скачивание модели DocLayout-YOLO

```bash
# Автоматическое скачивание при первом использовании
# Модель загрузится в ~/.cache/huggingface/

# Или ручное скачивание
pip install huggingface_hub
python -c "from huggingface_hub import hf_hub_download; hf_hub_download('opendatalab/DocLayout-YOLO', 'doclayout_yolo_docstructbench.pt')"

# Размер модели: ~25MB
```

---

## 18. Известные ограничения и TODO

### 18.1 Текущие ограничения

1. **Формулы**: Извлечение LaTeX-кода из формул требует OCR (Mathpix). В MVP формулы извлекаются как изображения.

2. **Сложные таблицы**: Таблицы с объединёнными ячейками (merged cells) могут обрабатываться некорректно.

3. **Rotated text**: Текст с rotation != 0 может потребовать специальной обработки.

4. **Scanned PDFs**: Документы без текстового слоя (только изображения) не поддерживаются.

5. **Interactive elements**: Формы, кнопки, JS-скрипты в PDF игнорируются.

6. **Digital signatures**: PDF с цифровыми подписями могут быть read-only.

### 18.2 TODO для будущих версий

- [ ] Интеграция с Mathpix API для извлечения формул как LaTeX
- [ ] Поддержка scanned PDFs через OCR
- [ ] GUI (web interface или desktop app)
- [ ] Batch processing (несколько файлов)
- [ ] Terminology memory (glossary across documents)
- [ ] A/B comparison view (оригинал и перевод рядом)
- [ ] Quality estimation (оценка качества перевода)

---

## 19. Примеры выходных файлов

### 19.1 Структура выходной директории

```
output/
├── document_ru.pdf           # Финальный переведённый PDF
├── debug/                    # (если --debug)
│   ├── geometry.json         # Извлечённая геометрия
│   ├── elements.json         # Извлечённые элементы
│   ├── content.json          # Извлечённый контент
│   ├── content_translated.json  # Переведённый контент
│   ├── document.tex          # Сгенерированный LaTeX
│   ├── document.log          # Лог компиляции
│   └── images/               # Извлечённые изображения
│       ├── logo_0.png
│       ├── figure_1.png
│       └── ...
```

---

## 20. Checklist для реализации

### Phase 0: ML Detection Setup
- [ ] Установка DocLayout-YOLO
- [ ] Скачивание весов модели (DocLayout-YOLO-DocStructBench)
- [ ] Layout Detector: базовая детекция элементов
- [ ] Layout Detector: конвертация координат image→PDF
- [ ] Cross-Page Analyzer: детекция продолжающихся параграфов
- [ ] Cross-Page Analyzer: построение семантических единиц
- [ ] Тест на multi-page документе

### Phase 1: Базовый pipeline
- [ ] Структура проекта
- [ ] Data models (dataclasses)
- [ ] GeometryExtractor (использует результаты Layout Detector)
- [ ] ContentExtractor (использует результаты Layout Detector)
- [ ] Простой LaTeX template
- [ ] PDF Compiler
- [ ] CLI interface
- [ ] Тест на простом single-column документе

### Phase 2: Продвинутая геометрия
- [ ] Детекция multi-column layout (улучшение через YOLO)
- [ ] Извлечение header/footer (из YOLO детекций)
- [ ] Специальная обработка титульной страницы
- [ ] Тест на arXiv-style документе

### Phase 3: Визуальные элементы
- [ ] ElementExtractor (изображения из YOLO: figures, logos)
- [ ] ElementExtractor (линии, боксы из PyMuPDF)
- [ ] ElementExtractor (формулы из YOLO: isolate_formula)
- [ ] Абсолютное позиционирование в LaTeX
- [ ] Тест на Frontiers-style документе

### Phase 4: Перевод
- [ ] Translator с API integration
- [ ] Интеграция Cross-Page Analyzer для контекстного перевода
- [ ] Сохранение citations, references
- [ ] Обработка edge cases (abbreviations, etc.)
- [ ] Тест качества перевода

### Phase 5: Полировка
- [ ] Font Analyzer
- [ ] Обработка ошибок
- [ ] Логирование
- [ ] Документация
- [ ] Comprehensive тесты
