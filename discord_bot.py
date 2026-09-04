import discord
from discord import app_commands
from discord.ext import commands
import os
import asyncio
import logging
import re
import hashlib
from pathlib import Path
from dotenv import load_dotenv

# Импортируем компоненты системы
from pipeline.translator import TranslationOrchestrator
from pipeline.arxiv_fetcher import ArxivFetcher
PDF_RECONSTRUCT_AVAILABLE = True
try:
    from pipeline.pdf_reconstruct.cli import _stage_input_pdf
    from pipeline.pdf_reconstruct.errors import ExternalToolError, OptionalDependencyError, PdfPipelineError
    from pipeline.pdf_reconstruct.pipeline import extract_pdf, normalize_bundle, render_bundle, translate_bundle
    from pipeline.pdf_reconstruct.url_resolver import resolve_pdf_url
except ModuleNotFoundError:
    PDF_RECONSTRUCT_AVAILABLE = False
    _stage_input_pdf = None
    ExternalToolError = RuntimeError
    OptionalDependencyError = RuntimeError
    PdfPipelineError = RuntimeError
    extract_pdf = None
    normalize_bundle = None
    render_bundle = None
    translate_bundle = None
    resolve_pdf_url = None
from config import Config
from utils.helpers import extract_arxiv_id
from integrations.notion_client import NotionClient
from integrations.openai_client import OpenAIClient

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("discord_bot")

# Загрузка переменных окружения
load_dotenv()
TOKEN = os.getenv("DISCORD_BOT_TOKEN")

if not TOKEN:
    print("❌ Ошибка: DISCORD_BOT_TOKEN не найден в .env файле.")
    print("Пожалуйста, добавьте его в .env файл: DISCORD_BOT_TOKEN=ваш_токен")
    exit(1)

# Настройка интентов
intents = discord.Intents.default()
intents.message_content = True

# Класс бота с поддержкой Slash-команд
class RosettaBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        # Синхронизация команд с серверами Discord
        await self.tree.sync()
        logger.info("Slash-команды синхронизированы")

bot = RosettaBot()

def get_article_title(arxiv_id: str) -> str:
    """Получает заголовок статьи по ID."""
    try:
        fetcher = ArxivFetcher()
        metadata = fetcher.extract_metadata(arxiv_id)
        return metadata.get('title', arxiv_id)
    except Exception as e:
        logger.warning(f"Не удалось получить заголовок для {arxiv_id}: {e}")
        return arxiv_id

def get_article_metadata(arxiv_id: str) -> dict:
    """Получает полные метаданные статьи по ID."""
    try:
        fetcher = ArxivFetcher()
        metadata = fetcher.extract_metadata(arxiv_id)
        return {
            'title': metadata.get('title', arxiv_id),
            'authors': metadata.get('authors', []),
            'categories': metadata.get('categories', []),
            'abstract': metadata.get('abstract', ''),
            'published_date': metadata.get('published_date')
        }
    except Exception as e:
        logger.warning(f"Не удалось получить метаданные для {arxiv_id}: {e}")
        return {
            'title': arxiv_id,
            'authors': [],
            'categories': [],
            'abstract': '',
            'published_date': None
        }

def extract_translated_abstract(arxiv_id: str) -> str:
    """Извлекает резюме из переведенного LaTeX файла."""
    try:
        translated_tex_path = Config.TEMP_DIR / arxiv_id / "source" / "translated.tex"
        if not translated_tex_path.exists():
            logger.warning(f"Переведенный файл не найден: {translated_tex_path}")
            return ""
        
        with open(translated_tex_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Ищем \begin{abstract}...\end{abstract}
        abstract_pattern = re.compile(
            r'\\begin\{abstract\}(.*?)\\end\{abstract\}',
            re.DOTALL
        )
        
        match = abstract_pattern.search(content)
        if not match:
            logger.warning("Не найдена секция abstract в переведенном файле")
            return ""
        
        abstract_text = match.group(1).strip()
        
        # 1. Удаляем все LaTeX комментарии (строки с %)
        lines = abstract_text.split('\n')
        cleaned_lines = []
        for line in lines:
            # Удаляем комментарии (всё после %)
            if '%' in line:
                comment_pos = line.find('%')
                # Проверяем, что это не часть URL или команды
                if comment_pos > 0 and line[comment_pos-1] != '\\':
                    line = line[:comment_pos].rstrip()
            if line.strip():
                cleaned_lines.append(line)
        abstract_text = '\n'.join(cleaned_lines)
        
        # 2. Удаляем LaTeX команды более агрессивно
        # Удаляем команды вида \command{...} и оставляем только содержимое
        abstract_text = re.sub(r'\\[a-zA-Z@]+(\[[^\]]*\])?\{([^}]*)\}', r'\2', abstract_text)
        # Удаляем оставшиеся команды без аргументов
        abstract_text = re.sub(r'\\[a-zA-Z@]+(\[[^\]]*\])?', '', abstract_text)
        # Удаляем специальные символы LaTeX
        abstract_text = re.sub(r'\\[{}]', '', abstract_text)
        abstract_text = re.sub(r'~', ' ', abstract_text)  # Неразрывный пробел -> обычный
        
        # 3. Удаляем технические детали (URL, TODO, имена пользователей и т.д.)
        abstract_text = re.sub(r'https?://[^\s]+', '', abstract_text)  # URL
        abstract_text = re.sub(r'%TODO[^\n]*', '', abstract_text, flags=re.IGNORECASE)
        abstract_text = re.sub(r'%[a-zA-Z0-9_@]+:', '', abstract_text)  # Комментарии вида %user:
        abstract_text = re.sub(r'Code available at.*?}', '', abstract_text, flags=re.DOTALL)
        
        # 4. Убираем лишние пробелы и переносы строк
        abstract_text = re.sub(r'\s+', ' ', abstract_text)
        abstract_text = abstract_text.strip()
        
        # 5. Ограничиваем длину для краткого описания (500 символов)
        # Ищем естественную точку обрыва (конец предложения)
        max_length = 500
        if len(abstract_text) > max_length:
            # Пытаемся обрезать на конце предложения
            truncated = abstract_text[:max_length]
            # Ищем последнюю точку, восклицательный или вопросительный знак
            last_sentence_end = max(
                truncated.rfind('.'),
                truncated.rfind('!'),
                truncated.rfind('?')
            )
            if last_sentence_end > max_length * 0.7:  # Если нашли в последних 30%
                abstract_text = abstract_text[:last_sentence_end + 1]
            else:
                abstract_text = truncated.rstrip() + "..."
        
        logger.info(f"Извлечено резюме из переведенного файла ({len(abstract_text)} символов)")
        return abstract_text
        
    except Exception as e:
        logger.error(f"Ошибка при извлечении резюме: {e}")
        return ""

def summarize_abstract(abstract_text: str) -> str:
    """Создает краткий пересказ резюме через GPT."""
    if not abstract_text or len(abstract_text.strip()) < 50:
        return abstract_text  # Если резюме слишком короткое, возвращаем как есть
    
    try:
        client = OpenAIClient()
        
        # Промпт для краткого пересказа
        system_prompt = """Ты помощник для создания кратких описаний научных статей. 
Создай краткий и понятный пересказ резюме научной статьи на русском языке.
Пересказ должен быть:
- Кратким (максимум 300-400 символов)
- Понятным для широкой аудитории
- Без технических деталей и метаданных
- Только суть исследования и основные выводы
- Без упоминания конкретных метрик, если они не критичны"""
        
        user_message = f"Создай краткий пересказ этого резюме научной статьи:\n\n{abstract_text}"
        
        # Делаем запрос к GPT напрямую
        response = client.client.chat.completions.create(
            model=client.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            temperature=0.3,
            max_tokens=200  # Ограничиваем длину ответа
        )
        
        summary = response.choices[0].message.content.strip()
        logger.info(f"Создан пересказ резюме ({len(summary)} символов)")
        return summary
        
    except Exception as e:
        logger.error(f"Ошибка при создании пересказа резюме: {e}")
        # В случае ошибки возвращаем оригинальное резюме, обрезанное до 400 символов
        if len(abstract_text) > 400:
            # Пытаемся обрезать на конце предложения
            truncated = abstract_text[:400]
            last_dot = max(truncated.rfind('.'), truncated.rfind('!'), truncated.rfind('?'))
            if last_dot > 300:
                return abstract_text[:last_dot + 1]
            return truncated.rstrip() + "..."
        return abstract_text

def run_translation_sync(arxiv_id: str, lang: str = "ru") -> dict:
    """
    Синхронная функция для запуска пайплайна перевода.
    Выполняется в отдельном потоке.
    """
    result = {
        "success": False,
        "original_pdf": None,
        "translated_pdf": None,
        "error": None
    }

    try:
        # Инициализируем оркестратор
        orchestrator = TranslationOrchestrator()
        
        # Запускаем перевод
        logger.info(f"Начало перевода для {arxiv_id} в фоновом потоке")
        success = orchestrator.translate_article(arxiv_id, target_lang=str(lang))
        
        if success:
            paper_dir = Config.TEMP_DIR / arxiv_id
            
            # Ищем оригинальный PDF
            original_pdf = paper_dir / f"{arxiv_id}_original.pdf"
            if original_pdf.exists():
                result["original_pdf"] = original_pdf
            
            # Ищем переведенный PDF (в папке source)
            translated_pdf = paper_dir / "source" / "translated.pdf"
            if translated_pdf.exists():
                result["translated_pdf"] = translated_pdf
                result["success"] = True
            else:
                result["error"] = "PDF файл перевода не найден после завершения процесса."
        else:
            result["error"] = "Процесс перевода завершился с ошибкой (см. логи консоли)."
            
    except Exception as e:
        logger.error(f"Исключение в процессе перевода: {e}")
        result["error"] = str(e)

    return result


def run_pdf_translation_sync(
    *,
    input_arg: str,
    lang: str,
    use_headless: bool,
) -> dict:
    result = {
        "success": False,
        "paper_dir": None,
        "original_pdf": None,
        "translated_pdf": None,
        "error": None,
    }

    try:
        if not PDF_RECONSTRUCT_AVAILABLE:
            result["error"] = "PDF translation pipeline is not available on this server."
            return result

        output_root = Config.OUTPUT_DIR / "pdf_articles"
        output_root.mkdir(parents=True, exist_ok=True)

        resolved = resolve_pdf_url(input_arg, use_headless=use_headless)
        digest = hashlib.sha1((resolved or input_arg).encode("utf-8", errors="ignore")).hexdigest()[:10]
        paper_dir = output_root / f"pdf_{digest}"

        pdf_path = _stage_input_pdf(resolved, paper_dir)
        bundle = extract_pdf(pdf_path=pdf_path, out_dir=paper_dir, extractor="auto")
        normalize_bundle(bundle)
        out_md = translate_bundle(bundle, target_lang=lang, skip_references=True)

        template_path = Path("templates") / "rosetta.latex"
        out_pdf = paper_dir / f"translated_{lang}.pdf"
        render_bundle(bundle, md_path=out_md, output_pdf_path=out_pdf, template_path=template_path, target_lang=lang)

        result["success"] = True
        result["paper_dir"] = paper_dir
        result["original_pdf"] = paper_dir / "original.pdf"
        result["translated_pdf"] = out_pdf
    except (PdfPipelineError, OptionalDependencyError, ExternalToolError) as e:
        result["error"] = str(e)
    except Exception as e:
        result["error"] = str(e)

    return result

@bot.event
async def on_ready():
    # Устанавливаем статус бота
    activity = discord.CustomActivity(name="Научный перевод")
    await bot.change_presence(status=discord.Status.online, activity=activity)
    
    logger.info(f'Бот запущен как {bot.user} (ID: {bot.user.id})')
    logger.info('Система Rosetta готова к приему команд /rosetta')

# Slash-команда /rosetta
def clean_filename(title: str) -> str:
    """Очищает заголовок для использования в имени файла."""
    # Оставляем только буквы, цифры и пробелы, обрезаем длину
    clean = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_')).strip()
    return clean[:50]  # Ограничение длины имени файла

@bot.tree.command(name="rosetta", description="Перевести научную статью с arXiv")
@app_commands.describe(arxiv_link="Ссылка на статью или arXiv ID (например, 1706.03762)", lang="Язык перевода (например, ru, fr, de)")
async def rosetta(interaction: discord.Interaction, arxiv_link: str, lang: str = "ru"):
    # Сразу отвечаем (defer), так как перевод займет время > 3 секунд
    await interaction.response.defer(thinking=True)

    # 1. Валидация
    arxiv_id = extract_arxiv_id(arxiv_link)
    if not arxiv_id:
        await interaction.followup.send("Ошибка: Некорректная ссылка или ID arXiv.")
        return

    # Получаем название статьи (в отдельном потоке, так как это сетевой запрос)
    loop = asyncio.get_running_loop()
    title = await loop.run_in_executor(None, get_article_title, arxiv_id)

    await interaction.followup.send(f"**Принято!** Начинаю работу над статьей **«{title}»**. Пришлю перевод как только закончу — обычно 7-10 минут.")

    try:
        # 2. Запуск в отдельном потоке
        result = await loop.run_in_executor(None, lambda: run_translation_sync(arxiv_id, lang=lang))

        # 3. Обработка результата
        if result["success"]:
            files_to_send = []
            large_files_info = []
            safe_title = clean_filename(title)
            MAX_DISCORD_SIZE = 8 * 1024 * 1024  # 8MB лимит Discord
            
            if result["original_pdf"]:
                original_path = Path(result["original_pdf"])
                if original_path.exists():
                    file_size = original_path.stat().st_size
                    filename_en = f"{safe_title} (EN).pdf"
                    if file_size > MAX_DISCORD_SIZE:
                        large_files_info.append(f"Original PDF: {file_size / (1024*1024):.1f}MB")
                    else:
                        files_to_send.append(discord.File(result["original_pdf"], filename=filename_en))
            
            if result["translated_pdf"]:
                translated_path = Path(result["translated_pdf"])
                if translated_path.exists():
                    file_size = translated_path.stat().st_size
                    suffix = (str(lang or "ru").strip().upper() or "RU")
                    filename_tr = f"{safe_title} ({suffix}).pdf"
                    if file_size > MAX_DISCORD_SIZE:
                        large_files_info.append(f"Translated PDF: {file_size / (1024*1024):.1f}MB")
                    else:
                        files_to_send.append(discord.File(result["translated_pdf"], filename=filename_tr))
            
            # Отправляем результат пользователю
            if files_to_send and not large_files_info:
                success_message = await interaction.followup.send(
                    content=f"Перевод статьи «{title}» готов!", 
                    files=files_to_send
                )
                logger.info("PDF файлы отправлены пользователю в Discord")
            elif large_files_info:
                # Формируем сообщение для больших файлов
                message_parts = [
                    f"Перевод статьи «{title}» готов!",
                    f"\n**Однако следующие файлы превышают лимит Discord (8MB):**"
                ]
                message_parts.extend(large_files_info)
                message_parts.extend([
                    f"\n**Файлы сохранены в:** {result.get('paper_dir', 'temp directory')}",
                    "\n**Решения:**",
                    "1. Используйте ссылку на arXiv вместо загрузки PDF",
                    "2. Файлы доступны локально на сервере"
                ])
                await interaction.followup.send("\n".join(message_parts))
                logger.info("Отправлено сообщение о больших файлах")
            else:
                success_message = await interaction.followup.send("Процесс завершен успешно, но файлы не найдены.")
            
            # 4. Отправка в Notion (не блокируем, если не удалось)
            notion_success = False
            try:
                if Config.NOTION_TOKEN and Config.NOTION_DATABASE_ID:
                    logger.info(f"Отправка перевода {arxiv_id} в Notion...")
                    # Получаем полные метаданные для Notion
                    article_metadata = await loop.run_in_executor(None, get_article_metadata, arxiv_id)
                    
                    # Извлекаем резюме из переведенного файла (на русском)
                    translated_abstract = await loop.run_in_executor(None, extract_translated_abstract, arxiv_id)
                    # Создаем краткий пересказ резюме через GPT
                    if translated_abstract:
                        abstract_to_use = await loop.run_in_executor(None, summarize_abstract, translated_abstract)
                    else:
                        # Если не удалось извлечь, используем оригинальное (на английском)
                        abstract_to_use = article_metadata.get('abstract', '')
                        if abstract_to_use:
                            # Делаем пересказ и для английского резюме
                            abstract_to_use = await loop.run_in_executor(None, summarize_abstract, abstract_to_use)
                    
                    notion_client = NotionClient(
                        token=Config.NOTION_TOKEN,
                        database_id=Config.NOTION_DATABASE_ID
                    )
                    
                    # Используем локальные файлы напрямую для Notion
                    # Новый API Notion (v1/file_uploads) позволяет загружать файлы напрямую
                    # Для больших файлов (>20MB) будет использовано внешнее хранилище
                    # Формируем имена файлов как в Discord
                    safe_title = clean_filename(title)
                    original_filename = f"{safe_title} (EN).pdf"
                    suffix = (str(lang or "ru").strip().upper() or "RU")
                    translated_filename = f"{safe_title} ({suffix}).pdf"
                    
                    notion_result = notion_client.create_translation_page(
                        title=article_metadata['title'],
                        arxiv_id=arxiv_id,
                        original_pdf_path=result.get("original_pdf"),
                        translated_pdf_path=result.get("translated_pdf"),
                        original_pdf_url=None,  # Не используем Discord URL - загружаем напрямую
                        translated_pdf_url=None,  # Не используем Discord URL - загружаем напрямую
                        original_pdf_filename=original_filename,  # Имя файла как в Discord
                        translated_pdf_filename=translated_filename,  # Имя файла как в Discord
                        metadata={
                            'authors': article_metadata.get('authors', []),
                            'categories': article_metadata.get('categories', []),
                            'abstract': abstract_to_use
                        }
                    )
                    
                    if notion_result.success:
                        notion_success = True
                        logger.info(f"Перевод успешно добавлен в Notion: {notion_result.page_url}")
                        # Обновляем сообщение, добавляя информацию о добавлении в базу знаний
                        if success_message and files_to_send:
                            await success_message.edit(
                                content=f"Перевод статьи «{title}» готов! Статья добавлена в базу знаний."
                            )
                    else:
                        logger.warning(f"Не удалось добавить перевод в Notion: {notion_result.error}")
                else:
                    logger.info("Notion не настроен (отсутствует NOTION_TOKEN или NOTION_DATABASE_ID)")
            except Exception as e:
                logger.error(f"Ошибка при отправке в Notion: {e}", exc_info=True)
                # Не прерываем выполнение, если Notion не работает
        else:
            error_msg = result.get("error", "Неизвестная ошибка")
            await interaction.followup.send(f"Ошибка перевода:\n{error_msg}")

    except Exception as e:
        logger.error(f"Ошибка в команде /rosetta: {e}")
        await interaction.followup.send(f"Критическая ошибка бота: {e}")


@bot.tree.command(name="rosetta_pdf", description="Перевести научную статью по PDF ссылке или PDF-файлу")
@app_commands.describe(link="Ссылка на статью или прямой PDF URL", pdf="PDF файл (attachment)", lang="Язык перевода (например, ru)", headless="Headless-режим для JS-сайтов")
async def rosetta_pdf(
    interaction: discord.Interaction,
    link: str = "",
    pdf: discord.Attachment | None = None,
    lang: str = "ru",
    headless: bool = False,
):
    await interaction.response.defer(thinking=True)

    if not PDF_RECONSTRUCT_AVAILABLE:
        await interaction.followup.send("PDF перевод сейчас недоступен на сервере.")
        return
 
    input_arg = (link or "").strip()
    upload_path: Path | None = None
    if pdf is not None:
        ct = (pdf.content_type or "").lower().strip()
        name = (pdf.filename or "").lower()
        looks_like_pdf = (ct in ("application/pdf", "application/x-pdf", "application/acrobat")) or name.endswith(".pdf")
        if not looks_like_pdf:
            await interaction.followup.send("Ошибка: attachment должен быть PDF.")
            return
        
        # Проверка размера файла перед загрузкой
        MAX_DISCORD_SIZE = 8 * 1024 * 1024  # 8MB лимит Discord
        if pdf.size and pdf.size > MAX_DISCORD_SIZE:
            await interaction.followup.send(
                f"Ошибка: PDF файл слишком большой ({pdf.size / (1024*1024):.1f}MB). "
                f"Лимит Discord: {MAX_DISCORD_SIZE / (1024*1024):.0f}MB.\n\n"
                f"**Решения:**\n"
                f"1. Используйте ссылку на arXiv вместо загрузки файла\n"
                f"2. Загрузите файл на облачное хранилище и пришлите ссылку"
            )
            return
        work_dir = Config.TEMP_DIR / "discord_uploads"
        work_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha1((pdf.url or pdf.filename or "").encode("utf-8", errors="ignore")).hexdigest()[:10]
        upload_path = work_dir / f"upload_{digest}.pdf"
        await pdf.save(upload_path)
        input_arg = str(upload_path)
    elif not input_arg:
        await interaction.followup.send("Пришли ссылку или прикрепи PDF файлом.")
        return

    loop = asyncio.get_running_loop()
    await interaction.followup.send("Принято! Пытаюсь получить PDF и запустить перевод. Это может занять несколько минут.")

    try:
        result = await loop.run_in_executor(
            None,
            lambda: run_pdf_translation_sync(input_arg=input_arg, lang=lang, use_headless=bool(headless)),
        )

        if not result.get("success"):
            err = result.get("error") or "Неизвестная ошибка"
            await interaction.followup.send(
                "Не удалось автоматически получить PDF/перевести по ссылке. "
                "Если сайт закрыт или ссылка генерируется через JS, прикрепи PDF файлом.\n\n"
                f"Ошибка: {err}"
            )
            return

        # Проверка размера файлов и подготовка к отправке
        files_to_send = []
        large_files_info = []
        MAX_DISCORD_SIZE = 8 * 1024 * 1024  # 8MB лимит Discord
        
        if result.get("original_pdf") and Path(result["original_pdf"]).exists():
            original_path = Path(result["original_pdf"])
            file_size = original_path.stat().st_size
            if file_size > MAX_DISCORD_SIZE:
                large_files_info.append(f"Original PDF: {file_size / (1024*1024):.1f}MB (превышает лимит Discord)")
            else:
                files_to_send.append(discord.File(result["original_pdf"], filename=f"Original ({lang}).pdf"))
                
        if result.get("translated_pdf") and Path(result["translated_pdf"]).exists():
            translated_path = Path(result["translated_pdf"])
            file_size = translated_path.stat().st_size
            if file_size > MAX_DISCORD_SIZE:
                large_files_info.append(f"Translated PDF: {file_size / (1024*1024):.1f}MB (превышает лимит Discord)")
            else:
                files_to_send.append(discord.File(result["translated_pdf"], filename=f"Translated ({lang}).pdf"))

        # Отправка результата
        if files_to_send and not large_files_info:
            await interaction.followup.send(content="Готово!", files=files_to_send)
        elif large_files_info:
            # Если есть большие файлы, отправляем сообщение с информацией
            message_parts = ["Готово! Однако некоторые файлы слишком большие для Discord:"]
            message_parts.extend(large_files_info)
            message_parts.append("\n**Решения:**")
            message_parts.append("1. Используйте arXiv ссылку вместо загрузки PDF")
            message_parts.append("2. Или запросите статью по частям (если возможно)")
            message_parts.append(f"\nФайлы сохранены в: {result.get('paper_dir', 'temp directory')}")
            
            await interaction.followup.send("\n".join(message_parts))
        elif files_to_send:
            await interaction.followup.send(content="Готово!", files=files_to_send)
        else:
            await interaction.followup.send("Процесс завершен, но PDF файлы не найдены.")
    finally:
        if upload_path is not None:
            try:
                upload_path.unlink(missing_ok=True)
            except Exception:
                pass

if __name__ == "__main__":
    bot.run(TOKEN)
