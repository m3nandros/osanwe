"""
Notion API client for Rosetta v3.

Handles interaction with Notion API for storing translation results:
- Creating pages in Notion database
- Uploading PDF files
- Adding metadata (title, arxiv_id, dates, etc.)
"""

import os
import tempfile
import requests
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass

from utils.logger import get_logger
from config import Config

logger = get_logger(__name__)


@dataclass
class NotionPageResult:
    """
    Result of creating a Notion page.
    
    Attributes:
        success: Whether the operation was successful
        page_id: ID of the created page (if successful)
        page_url: URL to the created page (if successful)
        error: Error message (if failed)
    """
    success: bool
    page_id: Optional[str] = None
    page_url: Optional[str] = None
    error: Optional[str] = None


class NotionClient:
    """
    Notion API client for storing translation results.
    
    Handles:
    - Creating pages in Notion database
    - Uploading and attaching PDF files
    - Setting page properties (title, arxiv_id, dates, etc.)
    """
    
    NOTION_API_BASE = "https://api.notion.com/v1"
    NOTION_VERSION = "2022-06-28"
    
    def __init__(self, token: Optional[str] = None, database_id: Optional[str] = None):
        """
        Initialize Notion client.
        
        Args:
            token: Notion integration token. If None, tries to load from env.
            database_id: Notion database ID. If None, tries to load from env.
        """
        self.token = token or os.getenv("NOTION_TOKEN")
        self.database_id = database_id or os.getenv("NOTION_DATABASE_ID")
        
        if not self.token:
            raise ValueError(
                "NOTION_TOKEN is not set. Please set it in .env file or pass as parameter."
            )
        
        if not self.database_id:
            raise ValueError(
                "NOTION_DATABASE_ID is not set. Please set it in .env file or pass as parameter."
            )
        
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": self.NOTION_VERSION,
            "Content-Type": "application/json"
        }
        
        self.logger = get_logger(__name__)
        self.logger.info("Notion client initialized")
    
    def find_page_by_arxiv_id(self, arxiv_id: str) -> Optional[str]:
        """
        Find existing page in database by arXiv ID.
        
        Args:
            arxiv_id: arXiv paper ID to search for
            
        Returns:
            Page ID if found, None otherwise
        """
        try:
            # Search for pages where link property contains the arxiv_id
            link_prop_name = Config.NOTION_PROP_LINK
            arxiv_url = f"https://arxiv.org/abs/{arxiv_id}"
            
            # Query database for pages with matching link
            # Notion API uses "equals" for URL properties
            query_data = {
                "filter": {
                    "property": link_prop_name,
                    "url": {
                        "equals": arxiv_url
                    }
                }
            }
            
            response = requests.post(
                f"{self.NOTION_API_BASE}/databases/{self.database_id}/query",
                headers=self.headers,
                json=query_data,
                timeout=30
            )
            
            if response.status_code == 200:
                results = response.json().get("results", [])
                if results:
                    page_id = results[0].get("id")
                    self.logger.info(f"Found existing page for {arxiv_id}: {page_id}")
                    return page_id
            else:
                error_data = response.json() if response.text else {}
                self.logger.warning(f"Failed to query database for {arxiv_id}: {response.status_code} - {error_data.get('message', '')}")
            
            return None
            
        except Exception as e:
            self.logger.error(f"Error searching for page by arxiv_id: {e}")
            return None
    
    def create_translation_page(
        self,
        title: str,
        arxiv_id: str,
        original_pdf_path: Optional[Path] = None,
        translated_pdf_path: Optional[Path] = None,
        original_pdf_url: Optional[str] = None,
        translated_pdf_url: Optional[str] = None,
        original_pdf_filename: Optional[str] = None,
        translated_pdf_filename: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> NotionPageResult:
        """
        Create a page in Notion database with translation results.
        
        Args:
            title: Article title
            arxiv_id: arXiv paper ID
            original_pdf_path: Path to original PDF file (optional, for local reference)
            translated_pdf_path: Path to translated PDF file (optional, for local reference)
            original_pdf_url: URL to original PDF file (optional, for Notion file block)
            translated_pdf_url: URL to translated PDF file (optional, for Notion file block)
            metadata: Additional metadata (authors, categories, etc.)
            
        Returns:
            NotionPageResult with success status and page info
        """
        try:
            # Check if page already exists
            existing_page_id = self.find_page_by_arxiv_id(arxiv_id)
            if existing_page_id:
                self.logger.info(f"Page for {arxiv_id} already exists, adding PDF files if needed")
                # Добавляем PDF файлы к существующей странице, если их еще нет
                if original_pdf_url or translated_pdf_url or original_pdf_path or translated_pdf_path:
                    self._upload_pdfs_to_page(
                        existing_page_id,
                        original_pdf_path,
                        translated_pdf_path,
                        original_pdf_url,
                        translated_pdf_url,
                        arxiv_id=arxiv_id
                    )
                return NotionPageResult(
                    success=True,
                    page_id=existing_page_id,
                    page_url=f"https://notion.so/{existing_page_id.replace('-', '')}"
                )
            
            self.logger.info(f"Creating Notion page for {arxiv_id}: {title}")
            
            # Build page properties
            properties = self._build_page_properties(title, arxiv_id, metadata or {})
            
            # Create page
            page_data = {
                "parent": {"database_id": self.database_id},
                "properties": properties
            }
            
            response = requests.post(
                f"{self.NOTION_API_BASE}/pages",
                headers=self.headers,
                json=page_data,
                timeout=30
            )
            
            if response.status_code != 200:
                error_data = response.json() if response.text else {}
                error_msg = error_data.get("message", response.text)
                error_code = error_data.get("code", "")
                
                # More detailed error message
                full_error = f"Failed to create page: {response.status_code}"
                if error_code:
                    full_error += f" (code: {error_code})"
                if error_msg:
                    full_error += f" - {error_msg}"
                
                self.logger.error(full_error)
                
                # Provide helpful hints for common errors
                if response.status_code == 400:
                    self.logger.error(
                        "Hint: Check that property names match your database schema exactly. "
                        "Also verify that select option values match existing options."
                    )
                elif response.status_code == 401:
                    self.logger.error("Hint: Check that NOTION_TOKEN is correct and has access to the database.")
                elif response.status_code == 404:
                    self.logger.error("Hint: Check that NOTION_DATABASE_ID is correct and the database exists.")
                
                return NotionPageResult(success=False, error=full_error)
            
            page_info = response.json()
            page_id = page_info.get("id")
            page_url = page_info.get("url")
            
            self.logger.info(f"Page created successfully: {page_id}")
            
            # Upload PDF files if provided (prefer URLs over local paths)
            if original_pdf_url or translated_pdf_url or original_pdf_path or translated_pdf_path:
                self._upload_pdfs_to_page(
                    page_id,
                    original_pdf_path,
                    translated_pdf_path,
                    original_pdf_url,
                    translated_pdf_url,
                    arxiv_id=arxiv_id,
                    original_pdf_filename=original_pdf_filename,
                    translated_pdf_filename=translated_pdf_filename
                )
            
            return NotionPageResult(
                success=True,
                page_id=page_id,
                page_url=page_url
            )
            
        except Exception as e:
            error_msg = f"Error creating Notion page: {str(e)}"
            self.logger.error(error_msg, exc_info=True)
            return NotionPageResult(success=False, error=error_msg)
    
    def _build_page_properties(
        self,
        title: str,
        arxiv_id: str,
        metadata: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Build Notion page properties from article data.
        
        Uses configurable property names from Config to match your database schema.
        Property names must match exactly the property names in your Notion database.
        
        Args:
            title: Article title
            arxiv_id: arXiv paper ID
            metadata: Additional metadata (authors, categories, abstract, etc.)
            
        Returns:
            Dictionary of Notion page properties
        """
        properties = {}
        
        # Title (first property, usually Title type)
        # Note: In Notion, the first property is typically the Title property
        title_prop_name = Config.NOTION_PROP_TITLE
        properties[title_prop_name] = {
            "title": [
                {
                    "text": {
                        "content": title
                    }
                }
            ]
        }
        
        # Авторы (Authors) - с жирным заголовком
        authors_prop_name = Config.NOTION_PROP_AUTHORS
        if "authors" in metadata and metadata["authors"]:
            authors_text = ", ".join(metadata["authors"][:10])  # Limit to 10 authors
            if len(metadata["authors"]) > 10:
                authors_text += f" и еще {len(metadata['authors']) - 10}"
            # Добавляем жирный заголовок "Авторы:" и затем список авторов
            properties[authors_prop_name] = {
                "rich_text": [
                    {
                        "text": {
                            "content": "Авторы: "
                        },
                        "annotations": {
                            "bold": True
                        }
                    },
                    {
                        "text": {
                            "content": authors_text
                        }
                    }
                ]
            }
        else:
            # Set empty if no authors
            properties[authors_prop_name] = {
                "rich_text": []
            }
        
        # Дата добавления (Date Added)
        date_prop_name = Config.NOTION_PROP_DATE
        properties[date_prop_name] = {
            "date": {
                "start": datetime.now().isoformat()
            }
        }
        
        # Резюме (Summary/Abstract) - с жирным заголовком
        summary_prop_name = Config.NOTION_PROP_SUMMARY
        if "abstract" in metadata and metadata["abstract"]:
            abstract = metadata["abstract"]
            # Limit abstract length (Notion has limits on rich_text length)
            if len(abstract) > 2000:
                abstract = abstract[:1997] + "..."
            # Добавляем жирный заголовок "Описание:"
            properties[summary_prop_name] = {
                "rich_text": [
                    {
                        "text": {
                            "content": "Описание: "
                        },
                        "annotations": {
                            "bold": True
                        }
                    },
                    {
                        "text": {
                            "content": abstract
                        }
                    }
                ]
            }
        else:
            properties[summary_prop_name] = {
                "rich_text": []
            }
        
        # Ссылка (Link) - используем arXiv ссылку
        link_prop_name = Config.NOTION_PROP_LINK
        properties[link_prop_name] = {
            "url": f"https://arxiv.org/abs/{arxiv_id}"
        }
        
        # Статус чтения (Reading Status)
        status_prop_name = Config.NOTION_PROP_STATUS
        default_status = Config.NOTION_DEFAULT_STATUS
        properties[status_prop_name] = {
            "select": {
                "name": default_status
            }
        }
        
        # Вид (Type) - статья/книга и пр. (multi_select)
        type_prop_name = Config.NOTION_PROP_TYPE
        default_type = Config.NOTION_DEFAULT_TYPE
        properties[type_prop_name] = {
            "multi_select": [
                {
                    "name": default_type
                }
            ]
        }
        
        # Тема (Topic/Categories) - multi_select
        topic_prop_name = Config.NOTION_PROP_TOPIC
        if "categories" in metadata and metadata["categories"]:
            # Преобразуем категории в multi_select формат
            categories = metadata["categories"]
            properties[topic_prop_name] = {
                "multi_select": [
                    {"name": cat} for cat in categories[:5]  # Ограничиваем до 5 категорий
                ]
            }
        else:
            properties[topic_prop_name] = {
                "multi_select": []
            }
        
        return properties
    
    def _upload_file_to_notion_direct(self, file_path: Path, filename: Optional[str] = None) -> Optional[str]:
        """
        Upload file directly to Notion using new v1/file_uploads API (May 2025).
        
        Process:
        1. Initialize upload (POST /v1/file_uploads)
        2. Upload file content (PUT to provided URL)
        3. Return file_upload_id for use in blocks
        
        Args:
            file_path: Path to the file to upload
            
        Returns:
            file_upload_id if successful, None otherwise
        """
        try:
            if not file_path.exists():
                self.logger.warning(f"File does not exist: {file_path}")
                return None
            
            file_size = file_path.stat().st_size
            file_size_mb = file_size / (1024 * 1024)
            
            # Notion limits:
            # - Free Plan: 5 MB per file
            # - Paid Plans (Plus, Business, Enterprise): up to 5 GB per file
            # We use configurable limit (default 5 MB) to ensure compatibility with Free Plan
            # For larger files, we'll use external storage
            if file_size_mb > Config.NOTION_DIRECT_UPLOAD_LIMIT_MB:
                self.logger.info(
                    f"File {file_path.name} is too large ({file_size_mb:.2f} MB) for direct upload. "
                    f"Configured limit: {Config.NOTION_DIRECT_UPLOAD_LIMIT_MB} MB "
                    f"(Free Plan: 5 MB, Paid Plans: up to 5 GB). Will use external storage instead."
                )
                return None
            
            # Step 1: Initialize upload
            # Use custom filename if provided, otherwise use file_path.name
            upload_filename = filename if filename else file_path.name
            init_data = {
                "filename": upload_filename,
                "content_type": "application/pdf",
                "file_size": file_size
            }
            
            self.logger.info(f"Initializing file upload for {upload_filename} ({file_size_mb:.2f} MB)...")
            init_response = requests.post(
                f"{self.NOTION_API_BASE}/file_uploads",
                headers=self.headers,
                json=init_data,
                timeout=30
            )
            
            if init_response.status_code != 200:
                error_data = init_response.json() if init_response.text else {}
                error_msg = error_data.get("message", init_response.text)
                
                # Provide helpful error messages
                if init_response.status_code == 400:
                    if "size" in error_msg.lower() or "limit" in error_msg.lower():
                        self.logger.warning(
                            f"File {file_path.name} exceeds Notion size limit. "
                            f"Free Plan: 5 MB, Paid Plans: 5 GB. "
                            f"File size: {file_size_mb:.2f} MB. Falling back to external storage."
                        )
                    else:
                        self.logger.warning(
                            f"Failed to initialize file upload (400): {error_msg}. "
                            f"Falling back to external storage."
                        )
                else:
                    self.logger.warning(
                        f"Failed to initialize file upload: {init_response.status_code} - {error_msg}. "
                        f"Falling back to external storage."
                    )
                return None
            
            init_result = init_response.json()
            file_upload_id = init_result.get("id")
            upload_url = init_result.get("upload_url")
            
            if not file_upload_id:
                self.logger.warning("No file_upload_id in response, falling back to external storage")
                return None
            
            if not upload_url:
                self.logger.warning("No upload_url in response, falling back to external storage")
                return None
            
            # Step 2: Upload file content
            self.logger.info(f"Uploading file content to Notion...")
            self.logger.debug(f"Upload URL: {upload_url[:100]}...")  # Log first 100 chars
            
            with open(file_path, 'rb') as f:
                file_content = f.read()
            
            # Check if upload_url is a Notion API endpoint or S3 presigned URL
            if 'api.notion.com' in upload_url:
                # Notion API endpoint - use POST with multipart/form-data
                # API expects: body.file should be defined
                upload_headers = {
                    "Authorization": f"Bearer {self.token}",
                    "Notion-Version": self.NOTION_VERSION
                    # Don't set Content-Type - requests will set it for multipart/form-data
                }
                
                # Use multipart/form-data with file field
                upload_filename = filename if filename else file_path.name
                with open(file_path, 'rb') as f:
                    files = {
                        'file': (upload_filename, f, 'application/pdf')
                    }
                    upload_response = requests.post(
                        upload_url,
                        files=files,
                        headers=upload_headers,
                        timeout=120
                    )
            elif 'amazonaws.com' in upload_url or 's3.' in upload_url:
                # S3 presigned URL - use PUT without headers (headers are in URL)
                upload_response = requests.put(
                    upload_url,
                    data=file_content,
                    timeout=120
                )
            else:
                # Unknown URL type - try PUT first, then POST
                upload_headers = {
                    "Content-Type": "application/pdf"
                }
                upload_response = requests.put(
                    upload_url,
                    data=file_content,
                    headers=upload_headers,
                    timeout=120
                )
                
                if upload_response.status_code not in (200, 204):
                    self.logger.debug(f"PUT failed ({upload_response.status_code}), trying POST...")
                    upload_response = requests.post(
                        upload_url,
                        data=file_content,
                        headers=upload_headers,
                        timeout=120
                    )
            
            if upload_response.status_code not in (200, 204):
                error_msg = ""
                try:
                    if upload_response.text:
                        error_data = upload_response.json() if upload_response.headers.get('content-type', '').startswith('application/json') else {}
                        error_msg = error_data.get("message", upload_response.text[:200])
                except:
                    error_msg = upload_response.text[:200] if upload_response.text else ""
                
                self.logger.warning(
                    f"Failed to upload file content: {upload_response.status_code}. "
                    f"{'Error: ' + error_msg if error_msg else ''} "
                    f"Upload URL type: {'S3 presigned' if 'amazonaws.com' in upload_url or 's3.' in upload_url else 'Other'}. "
                    f"Falling back to external storage."
                )
                # Log more details for debugging
                self.logger.debug(f"Upload URL (first 200 chars): {upload_url[:200]}")
                self.logger.debug(f"Response headers: {dict(upload_response.headers)}")
                return None
            
            upload_filename = filename if filename else file_path.name
            self.logger.info(f"Successfully uploaded {upload_filename} to Notion (ID: {file_upload_id})")
            return file_upload_id
            
        except Exception as e:
            self.logger.warning(f"Error uploading file to Notion directly: {e}. Falling back to external storage.")
            return None
    
    def _get_existing_file_blocks(self, page_id: str) -> Dict[str, bool]:
        """
        Get existing file blocks from a Notion page to avoid duplicates.
        
        Args:
            page_id: Notion page ID
            
        Returns:
            Dictionary mapping filenames/identifiers to whether they exist on the page
        """
        existing_files = {}
        try:
            # Get all blocks from the page (paginated)
            all_blocks = []
            next_cursor = None
            
            while True:
                params = {"page_size": 100}
                if next_cursor:
                    params["start_cursor"] = next_cursor
                
                response = requests.get(
                    f"{self.NOTION_API_BASE}/blocks/{page_id}/children",
                    headers=self.headers,
                    params=params,
                    timeout=30
                )
                
                if response.status_code != 200:
                    break
                
                data = response.json()
                results = data.get("results", [])
                all_blocks.extend(results)
                
                # Check if there are more pages
                has_more = data.get("has_more", False)
                next_cursor = data.get("next_cursor")
                
                if not has_more or not next_cursor:
                    break
            
            # Process all file blocks
            for block in all_blocks:
                if block.get("type") == "file":
                    file_data = block.get("file", {})
                    if file_data:
                        # Get caption text
                        caption = file_data.get("caption", [])
                        caption_text = ""
                        if caption and len(caption) > 0:
                            caption_text = caption[0].get("plain_text", "") or caption[0].get("text", {}).get("content", "")
                        
                        # Check for file_upload type
                        if "file_upload" in file_data:
                            file_upload = file_data.get("file_upload", {})
                            # Check if we can get filename from file_upload data
                            if caption_text:
                                existing_files[caption_text.lower()] = True
                            # Also mark as original/translated based on caption
                            if "оригинальный" in caption_text.lower() or "original" in caption_text.lower():
                                existing_files["original"] = True
                            if "переведенный" in caption_text.lower() or "translated" in caption_text.lower():
                                existing_files["translated"] = True
                        
                        # Check for external type
                        elif "external" in file_data:
                            external = file_data.get("external", {})
                            url = external.get("url", "")
                            
                            if caption_text:
                                existing_files[caption_text.lower()] = True
                            
                            # Check URL for patterns
                            url_lower = url.lower()
                            if "original" in url_lower or "_original" in url_lower:
                                existing_files["original"] = True
                            if "translated" in url_lower or "_translated" in url_lower:
                                existing_files["translated"] = True
                            
                            # Check caption for original/translated
                            if "оригинальный" in caption_text.lower() or "original" in caption_text.lower():
                                existing_files["original"] = True
                            if "переведенный" in caption_text.lower() or "translated" in caption_text.lower():
                                existing_files["translated"] = True
                            
                            # Extract filename from URL if possible
                            if "/" in url:
                                url_filename = url.split("/")[-1].split("?")[0]  # Remove query params
                                if url_filename and url_filename.endswith(".pdf"):
                                    existing_files[url_filename.lower()] = True
                                    # Also store without extension for matching
                                    existing_files[url_filename.lower().replace(".pdf", "")] = True
                                    
        except Exception as e:
            self.logger.warning(f"Error checking existing blocks: {e}")
        
        self.logger.debug(f"Found existing files on page: {list(existing_files.keys())}")
        return existing_files
    
    def _upload_pdfs_to_page(
        self,
        page_id: str,
        original_pdf_path: Optional[Path],
        translated_pdf_path: Optional[Path],
        original_pdf_url: Optional[str] = None,
        translated_pdf_url: Optional[str] = None,
        arxiv_id: Optional[str] = None,
        original_pdf_filename: Optional[str] = None,
        translated_pdf_filename: Optional[str] = None
    ):
        """
        Add PDF files to Notion page as file blocks.
        
        IMPORTANT: Files are added WITHOUT any heading/header (e.g., "PDF Файлы").
        Only file blocks are added directly to the page.
        
        Priority:
        1. Use local file paths to upload files directly to Notion (if supported)
        2. Use external URLs (e.g., from Discord) as external file blocks
        3. Fall back to text references if neither is available
        
        Args:
            page_id: Notion page ID
            original_pdf_path: Path to original PDF (will be uploaded if URL not available)
            translated_pdf_path: Path to translated PDF (will be uploaded if URL not available)
            original_pdf_url: URL to original PDF (preferred, will create external file block)
            translated_pdf_url: URL to translated PDF (preferred, will create external file block)
        """
        try:
            # Check for existing file blocks to avoid duplicates
            existing_files = self._get_existing_file_blocks(page_id)
            
            blocks = []
            
            # Check if we have any files to add
            has_files = (original_pdf_url or translated_pdf_url or 
                       (original_pdf_path and original_pdf_path.exists()) or
                       (translated_pdf_path and translated_pdf_path.exists()))
            
            if not has_files:
                self.logger.warning("No PDF files to add to Notion page")
                return
            
            # ВАЖНО: Не добавляем заголовок "PDF Файлы" - файлы добавляются напрямую без заголовка
            # Only file blocks are added, no heading blocks (heading_1, heading_2, etc.)
            
            # Determine upload strategy
            upload_strategy = Config.NOTION_FILE_UPLOAD_STRATEGY.lower()
            
            # Check if original PDF already exists
            # Check by various identifiers
            original_exists = False
            if "original" in existing_files:
                original_exists = True
            else:
                # Check by filename
                check_names = []
                if original_pdf_filename:
                    check_names.append(original_pdf_filename.lower())
                if original_pdf_path:
                    check_names.append(original_pdf_path.name.lower())
                    # Also check without extension
                    check_names.append(original_pdf_path.stem.lower())
                
                # Check if any of these names match existing files
                for check_name in check_names:
                    if check_name in existing_files:
                        original_exists = True
                        break
                    # Also check if any existing file contains the name or vice versa
                    for existing_key in existing_files.keys():
                        if check_name in existing_key or existing_key in check_name:
                            # Additional check: make sure it's not just a partial match
                            if len(check_name) > 5 and len(existing_key) > 5:  # Avoid false positives
                                original_exists = True
                                break
                    if original_exists:
                        break
            
            # Add original PDF
            if original_exists:
                self.logger.info("Original PDF already exists on page, skipping")
            elif upload_strategy == "discord_only":
                # Only use Discord URLs, don't try alternatives
                if original_pdf_url:
                    blocks.append({
                        "object": "block",
                        "type": "file",
                        "file": {
                            "type": "external",
                            "external": {"url": original_pdf_url}
                        }
                    })
                    self.logger.info(f"Added original PDF URL to page: {original_pdf_url}")
                else:
                    self.logger.warning("Original PDF URL not available (discord_only strategy)")
            elif upload_strategy == "temporary" or upload_strategy == "auto":
                # Always use local files for upload (direct or external storage)
                # For "auto", prefer local files over Discord URLs
                self.logger.info(f"Strategy: {upload_strategy}, checking original PDF...")
                self.logger.info(f"  original_pdf_path: {original_pdf_path}")
                self.logger.info(f"  original_pdf_path exists: {original_pdf_path.exists() if original_pdf_path else False}")
                self.logger.info(f"  original_pdf_url: {original_pdf_url}")
                
                if original_pdf_path and original_pdf_path.exists():
                    self.logger.info(f"Using local file for original PDF: {original_pdf_path}")
                    # Используем переданное имя файла или пустую подпись
                    caption = ""  # Без подписи
                    self._add_local_pdf_block(blocks, original_pdf_path, caption, arxiv_id, original_pdf_filename)
                elif original_pdf_url:
                    # Fallback to Discord URL if local file doesn't exist
                    self.logger.info(f"Using Discord URL for original PDF (fallback): {original_pdf_url}")
                    blocks.append({
                        "object": "block",
                        "type": "file",
                        "file": {
                            "type": "external",
                            "external": {"url": original_pdf_url}
                        }
                    })
                    self.logger.info(f"Added original PDF URL to page (fallback): {original_pdf_url}")
                else:
                    self.logger.warning("Original PDF path and URL not available")
            
            # Check if translated PDF already exists
            # Check by various identifiers
            translated_exists = False
            if "translated" in existing_files:
                translated_exists = True
            else:
                # Check by filename
                check_names = []
                if translated_pdf_filename:
                    check_names.append(translated_pdf_filename.lower())
                if translated_pdf_path:
                    check_names.append(translated_pdf_path.name.lower())
                    # Also check without extension
                    check_names.append(translated_pdf_path.stem.lower())
                
                # Check if any of these names match existing files
                for check_name in check_names:
                    if check_name in existing_files:
                        translated_exists = True
                        break
                    # Also check if any existing file contains the name or vice versa
                    for existing_key in existing_files.keys():
                        if check_name in existing_key or existing_key in check_name:
                            # Additional check: make sure it's not just a partial match
                            if len(check_name) > 5 and len(existing_key) > 5:  # Avoid false positives
                                translated_exists = True
                                break
                    if translated_exists:
                        break
            
            # Add translated PDF (same strategy as original)
            if translated_exists:
                self.logger.info("Translated PDF already exists on page, skipping")
            elif upload_strategy == "discord_only":
                if translated_pdf_url:
                    blocks.append({
                        "object": "block",
                        "type": "file",
                        "file": {
                            "type": "external",
                            "external": {"url": translated_pdf_url}
                        }
                    })
                    self.logger.info(f"Added translated PDF URL to page: {translated_pdf_url}")
                else:
                    self.logger.warning("Translated PDF URL not available (discord_only strategy)")
            elif upload_strategy == "temporary" or upload_strategy == "auto":
                # Always use local files for upload (direct or external storage)
                # For "auto", prefer local files over Discord URLs
                self.logger.info(f"Strategy: {upload_strategy}, checking translated PDF...")
                self.logger.info(f"  translated_pdf_path: {translated_pdf_path}")
                self.logger.info(f"  translated_pdf_path exists: {translated_pdf_path.exists() if translated_pdf_path else False}")
                self.logger.info(f"  translated_pdf_url: {translated_pdf_url}")
                
                if translated_pdf_path and translated_pdf_path.exists():
                    self.logger.info(f"Using local file for translated PDF: {translated_pdf_path}")
                    # Используем переданное имя файла или пустую подпись
                    caption = ""  # Без подписи
                    self._add_local_pdf_block(blocks, translated_pdf_path, caption, arxiv_id, translated_pdf_filename)
                elif translated_pdf_url:
                    # Fallback to Discord URL if local file doesn't exist
                    self.logger.info(f"Using Discord URL for translated PDF (fallback): {translated_pdf_url}")
                    blocks.append({
                        "object": "block",
                        "type": "file",
                        "file": {
                            "type": "external",
                            "external": {"url": translated_pdf_url}
                        }
                    })
                    self.logger.info(f"Added translated PDF URL to page (fallback): {translated_pdf_url}")
                else:
                    self.logger.warning("Translated PDF path and URL not available")
            
            # Add blocks to page if any
            if blocks:
                # Remove Content-Type header for PATCH request (let requests set it)
                headers = {
                    "Authorization": f"Bearer {self.token}",
                    "Notion-Version": self.NOTION_VERSION
                }
                
                response = requests.patch(
                    f"{self.NOTION_API_BASE}/blocks/{page_id}/children",
                    headers=headers,
                    json={"children": blocks},
                    timeout=60  # Increased timeout for file uploads
                )
                
                if response.status_code == 200:
                    self.logger.info(f"Successfully added {len(blocks)} block(s) to page")
                else:
                    error_data = response.json() if response.text else {}
                    error_msg = error_data.get("message", response.text)
                    self.logger.warning(
                        f"Failed to add blocks to page: {response.status_code} - {error_msg}"
                    )
                    # Log the blocks we tried to add for debugging
                    self.logger.debug(f"Blocks attempted: {blocks}")
            
        except Exception as e:
            self.logger.error(f"Error adding PDF references to page: {e}", exc_info=True)
            # Don't fail the whole operation if file reference fails
    
    def _download_discord_file(self, discord_url: str, output_path: Path) -> bool:
        """
        Download a file from Discord CDN to local storage.
        
        Args:
            discord_url: Discord CDN URL
            output_path: Path to save the file
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Discord CDN may require User-Agent header
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            response = requests.get(discord_url, headers=headers, timeout=60, stream=True)
            
            if response.status_code == 200:
                with open(output_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                self.logger.info(f"Downloaded file from Discord to {output_path}")
                return True
            else:
                self.logger.warning(f"Failed to download from Discord: {response.status_code}")
                return False
                
        except Exception as e:
            self.logger.error(f"Error downloading from Discord: {e}")
            return False
    
    def _upload_to_fileio(self, file_path: Path) -> Optional[str]:
        """Upload to file.io (100MB limit, expires after 1 download or 14 days)."""
        try:
            with open(file_path, 'rb') as f:
                files = {'file': (file_path.name, f, 'application/pdf')}
                response = requests.post('https://file.io', files=files, timeout=60)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('success'):
                    return data.get('link')
        except Exception as e:
            self.logger.debug(f"file.io upload failed: {e}")
        return None
    
    def _upload_to_transfersh(self, file_path: Path) -> Optional[str]:
        """Upload to transfer.sh (10GB limit, expires after 14 days)."""
        try:
            with open(file_path, 'rb') as f:
                response = requests.put(
                    f'https://transfer.sh/{file_path.name}',
                    data=f,
                    headers={'Max-Downloads': '100', 'Max-Days': '14'},
                    timeout=60
                )
            
            if response.status_code == 200:
                url = response.text.strip()
                return url
        except Exception as e:
            self.logger.debug(f"transfer.sh upload failed: {e}")
        return None
    
    def _upload_to_0x0(self, file_path: Path) -> Optional[str]:
        """Upload to 0x0.st (512MB limit, expires after 1 download or 30 days)."""
        try:
            with open(file_path, 'rb') as f:
                files = {'file': (file_path.name, f, 'application/pdf')}
                response = requests.post('https://0x0.st', files=files, timeout=60)
            
            if response.status_code == 200:
                url = response.text.strip()
                if url.startswith('http'):
                    return url
        except Exception as e:
            self.logger.debug(f"0x0.st upload failed: {e}")
        return None
    
    def _upload_to_s3_private(self, file_path: Path) -> Optional[str]:
        """
        Upload file to AWS S3 private bucket and return presigned URL.
        
        This creates a private, time-limited URL that only works for authorized users.
        The file itself is stored in a private S3 bucket.
        
        Args:
            file_path: Path to the file to upload
            
        Returns:
            Presigned URL if successful, None otherwise
        """
        try:
            import boto3
            from botocore.exceptions import ClientError
            from datetime import timedelta
            
            # Check if AWS credentials are configured
            if not Config.AWS_ACCESS_KEY_ID or not Config.AWS_SECRET_ACCESS_KEY or not Config.AWS_S3_BUCKET:
                self.logger.warning("AWS S3 credentials not configured. Skipping S3 upload.")
                return None
            
            # Initialize S3 client
            s3_client = boto3.client(
                's3',
                aws_access_key_id=Config.AWS_ACCESS_KEY_ID,
                aws_secret_access_key=Config.AWS_SECRET_ACCESS_KEY,
                region_name=Config.AWS_S3_REGION
            )
            
            # Generate unique key for the file
            import hashlib
            import time
            file_hash = hashlib.md5(file_path.read_bytes()).hexdigest()
            s3_key = f"rosetta-pdfs/{file_path.stem}_{file_hash[:8]}.pdf"
            
            # Upload file to S3
            self.logger.info(f"Uploading {file_path.name} to S3 bucket {Config.AWS_S3_BUCKET}...")
            s3_client.upload_file(
                str(file_path),
                Config.AWS_S3_BUCKET,
                s3_key,
                ExtraArgs={'ContentType': 'application/pdf'}
            )
            
            # Generate presigned URL (private, time-limited)
            expiry = timedelta(seconds=Config.AWS_S3_PRESIGNED_URL_EXPIRY)
            presigned_url = s3_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': Config.AWS_S3_BUCKET, 'Key': s3_key},
                ExpiresIn=int(expiry.total_seconds())
            )
            
            self.logger.info(f"Successfully uploaded to S3. Presigned URL expires in {expiry.days} days.")
            return presigned_url
            
        except ImportError:
            self.logger.warning("boto3 not installed. Install it with: pip install boto3")
            return None
        except Exception as e:
            self.logger.error(f"Error uploading to S3: {e}")
            return None
    
    def _upload_to_s3_public(self, file_path: Path) -> Optional[str]:
        """
        Upload file to AWS S3 bucket with public access and return permanent URL.
        
        This creates a permanent, public URL that never expires.
        The file is stored in an S3 bucket with public read access.
        
        Args:
            file_path: Path to the file to upload
            
        Returns:
            Permanent public URL if successful, None otherwise
        """
        try:
            import boto3
            from botocore.exceptions import ClientError
            
            # Check if AWS credentials are configured
            if not Config.AWS_ACCESS_KEY_ID or not Config.AWS_SECRET_ACCESS_KEY or not Config.AWS_S3_BUCKET:
                self.logger.warning("AWS S3 credentials not configured. Skipping S3 upload.")
                return None
            
            # Initialize S3 client
            s3_client = boto3.client(
                's3',
                aws_access_key_id=Config.AWS_ACCESS_KEY_ID,
                aws_secret_access_key=Config.AWS_SECRET_ACCESS_KEY,
                region_name=Config.AWS_S3_REGION
            )
            
            # Generate unique key for the file
            import hashlib
            file_hash = hashlib.md5(file_path.read_bytes()).hexdigest()
            s3_key = f"rosetta-pdfs/{file_path.stem}_{file_hash[:8]}.pdf"
            
            # Upload file to S3 with public read access
            self.logger.info(f"Uploading {file_path.name} to S3 bucket {Config.AWS_S3_BUCKET} (public access)...")
            s3_client.upload_file(
                str(file_path),
                Config.AWS_S3_BUCKET,
                s3_key,
                ExtraArgs={
                    'ContentType': 'application/pdf',
                    'ACL': 'public-read'  # Make file publicly readable
                }
            )
            
            # Generate permanent public URL
            if Config.AWS_S3_REGION == 'us-east-1':
                public_url = f"https://{Config.AWS_S3_BUCKET}.s3.amazonaws.com/{s3_key}"
            else:
                public_url = f"https://{Config.AWS_S3_BUCKET}.s3.{Config.AWS_S3_REGION}.amazonaws.com/{s3_key}"
            
            self.logger.info(f"Successfully uploaded to S3. Permanent public URL: {public_url}")
            return public_url
            
        except ImportError:
            self.logger.warning("boto3 not installed. Install it with: pip install boto3")
            return None
        except Exception as e:
            self.logger.error(f"Error uploading to S3: {e}")
            return None
    
    def _save_to_permanent_storage(self, file_path: Path, arxiv_id: str, file_type: str = "translated") -> Optional[str]:
        """
        Save file to permanent local storage and return URL.
        
        Files are organized by arxiv_id and stored permanently.
        Requires PERMANENT_STORAGE_URL_BASE to be configured for web access.
        
        Args:
            file_path: Path to the file to save
            arxiv_id: arXiv ID for organizing files
            file_type: Type of file ("original" or "translated")
            
        Returns:
            URL if PERMANENT_STORAGE_URL_BASE is configured, None otherwise
        """
        try:
            # Create directory structure: permanent_storage/arxiv_id/
            storage_dir = Config.PERMANENT_STORAGE_DIR / arxiv_id
            storage_dir.mkdir(parents=True, exist_ok=True)
            
            # Copy file to permanent storage
            dest_path = storage_dir / f"{file_type}_{arxiv_id}.pdf"
            import shutil
            shutil.copy2(file_path, dest_path)
            
            self.logger.info(f"Saved {file_path.name} to permanent storage: {dest_path}")
            
            # Generate URL if base URL is configured
            if Config.PERMANENT_STORAGE_URL_BASE:
                file_url = f"{Config.PERMANENT_STORAGE_URL_BASE.rstrip('/')}/{arxiv_id}/{file_type}_{arxiv_id}.pdf"
                return file_url
            else:
                self.logger.warning(
                    "PERMANENT_STORAGE_URL_BASE not configured. "
                    "File saved locally but no URL available. "
                    "Configure PERMANENT_STORAGE_URL_BASE to enable web access."
                )
                return None
                
        except Exception as e:
            self.logger.error(f"Error saving to permanent storage: {e}")
            return None
    
    def _upload_to_temporary_storage(self, file_path: Path) -> Optional[str]:
        """
        Upload file to storage service and return the URL.
        
        Supports both public temporary storage and private S3 storage.
        
        Args:
            file_path: Path to the file to upload
            
        Returns:
            URL if successful, None otherwise
        """
        try:
            storage_preference = Config.NOTION_TEMP_STORAGE.lower()
            
            # Permanent public S3 storage (permanent URLs)
            if storage_preference == "s3_public":
                return self._upload_to_s3_public(file_path)
            
            # Private storage (AWS S3 with presigned URLs)
            if storage_preference == "s3":
                return self._upload_to_s3_private(file_path)
            
            # Permanent local storage (handled in _add_local_pdf_block with arxiv_id)
            if storage_preference == "permanent":
                # This will be handled in _add_local_pdf_block
                return None
            
            # Local storage only (no public URL)
            if storage_preference == "local":
                self.logger.info(f"Using local storage only for {file_path.name} (no public URL)")
                # Return None - file stays local, we'll add a text reference instead
                return None
            
            # Public temporary storage services
            file_size = file_path.stat().st_size
            file_size_mb = file_size / (1024 * 1024)
            
            # Check file size limits
            if file_size_mb > 10240:  # 10GB (transfer.sh limit)
                self.logger.warning(f"File too large for temporary storage: {file_size_mb:.2f} MB")
                return None
            
            # Try services based on preference
            services_to_try = []
            
            if storage_preference == "auto":
                # Try all services in order of reliability
                services_to_try = [
                    ("transfer.sh", self._upload_to_transfersh),
                    ("0x0.st", self._upload_to_0x0),
                    ("file.io", self._upload_to_fileio),
                ]
            elif storage_preference == "transfersh":
                services_to_try = [("transfer.sh", self._upload_to_transfersh)]
            elif storage_preference == "0x0":
                services_to_try = [("0x0.st", self._upload_to_0x0)]
            elif storage_preference == "fileio":
                services_to_try = [("file.io", self._upload_to_fileio)]
            else:
                # Default to auto
                services_to_try = [
                    ("transfer.sh", self._upload_to_transfersh),
                    ("0x0.st", self._upload_to_0x0),
                    ("file.io", self._upload_to_fileio),
                ]
            
            # Try each service
            for service_name, upload_func in services_to_try:
                # Check size limits for specific services
                if service_name == "file.io" and file_size_mb > 100:
                    continue
                if service_name == "0x0.st" and file_size_mb > 512:
                    continue
                
                self.logger.info(f"Trying to upload to {service_name}...")
                url = upload_func(file_path)
                
                if url:
                    self.logger.info(f"Successfully uploaded {file_path.name} to {service_name}: {url}")
                    return url
                else:
                    self.logger.debug(f"{service_name} upload failed, trying next service...")
            
            self.logger.warning("All temporary storage services failed")
            return None
            
        except Exception as e:
            self.logger.error(f"Error uploading to storage: {e}")
            return None
    
    def _add_local_pdf_block(self, blocks: List[Dict], file_path: Path, caption: str, arxiv_id: Optional[str] = None, filename: Optional[str] = None):
        """
        Add a local PDF file block to Notion.
        
        Uses new v1/file_uploads API (May 2025) for direct file uploads when possible.
        Falls back to external storage for large files or if direct upload fails.
        
        Priority:
        1. Direct upload to Notion (files ≤ 20MB) - NEW API
        2. External storage (S3, permanent, temporary) - for large files or fallback
        3. Text block - if all fails
        
        Args:
            blocks: List of blocks to append to
            file_path: Path to the PDF file
            caption: Caption for the file block
            arxiv_id: arXiv ID for permanent storage (if needed)
        """
        try:
            file_size = file_path.stat().st_size
            file_size_mb = file_size / (1024 * 1024)
            
            storage_preference = Config.NOTION_TEMP_STORAGE.lower()
            
            # Local storage only - don't upload, just add text reference
            if storage_preference == "local":
                blocks.append({
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [
                            {
                                "text": {
                                    "content": f"{caption}: "
                                },
                                "annotations": {
                                    "bold": True
                                }
                            },
                            {
                                "text": {
                                    "content": f"{file_path.name} ({file_size_mb:.2f} MB) - хранится локально"
                                }
                            }
                        ]
                    }
                })
                self.logger.info(f"Added local file reference for {file_path.name} (local storage mode)")
                return
            
            # Step 1: Try direct upload to Notion first (for files ≤ limit to work on all plans)
            # Note: Free Plan limit is 5 MB, Paid Plans support up to 5 GB
            # We use configurable limit (default 5 MB) to ensure compatibility with Free Plan
            if file_size_mb <= Config.NOTION_DIRECT_UPLOAD_LIMIT_MB:
                file_upload_id = self._upload_file_to_notion_direct(file_path, filename)
                
                if file_upload_id:
                    # Use new file_upload type
                    upload_filename = filename if filename else file_path.name
                    file_block = {
                        "object": "block",
                        "type": "file",
                        "file": {
                            "type": "file_upload",
                            "file_upload": {
                                "id": file_upload_id
                            }
                        }
                    }
                    # Add caption only if it's not empty
                    if caption:
                        file_block["file"]["caption"] = [
                            {
                                "text": {
                                    "content": caption
                                }
                            }
                        ]
                    blocks.append(file_block)
                    self.logger.info(f"Added {upload_filename} to page via direct Notion upload (ID: {file_upload_id})")
                    return
            
            # Step 2: Fallback to external storage for large files or if direct upload failed
            self.logger.info(f"Using external storage for {file_path.name} (size: {file_size_mb:.2f} MB)")
            
            # Try to upload to storage (S3, public services, permanent storage, etc.)
            # For permanent storage, we need arxiv_id
            if storage_preference == "permanent" and arxiv_id:
                file_type = "original" if "original" in file_path.name.lower() or "(EN)" in file_path.name else "translated"
                file_url = self._save_to_permanent_storage(file_path, arxiv_id, file_type)
            else:
                file_url = self._upload_to_temporary_storage(file_path)
            
            if file_url:
                # Create file block with external URL
                upload_filename = filename if filename else file_path.name
                file_block = {
                    "object": "block",
                    "type": "file",
                    "file": {
                        "type": "external",
                        "external": {
                            "url": file_url
                        }
                    }
                }
                # Add caption only if it's not empty
                if caption:
                    file_block["file"]["caption"] = [
                        {
                            "text": {
                                "content": caption
                            }
                        }
                    ]
                blocks.append(file_block)
                storage_type = "private S3" if storage_preference == "s3" else "S3 public" if storage_preference == "s3_public" else "permanent" if storage_preference == "permanent" else "temporary storage"
                self.logger.info(f"Added local PDF {upload_filename} to page via {storage_type}")
            else:
                # Fall back to text block with file info
                blocks.append({
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [
                            {
                                "text": {
                                    "content": f"{caption}: "
                                },
                                "annotations": {
                                    "bold": True
                                }
                            },
                            {
                                "text": {
                                    "content": f"{file_path.name} ({file_size_mb:.2f} MB) - не удалось загрузить"
                                }
                            }
                        ]
                    }
                })
                self.logger.warning(
                    f"Could not upload local file {file_path.name} to storage. "
                    f"File block not created."
                )
            
        except Exception as e:
            self.logger.error(f"Error adding local PDF block: {e}", exc_info=True)
            # Add a simple text block as fallback
            blocks.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [
                        {
                            "text": {
                                "content": f"{caption}: файл {file_path.name} (ошибка загрузки)"
                            }
                        }
                    ]
                }
            })
    
    def update_page_status(self, page_id: str, status: str, error_message: Optional[str] = None):
        """
        Update page status in Notion.
        
        Args:
            page_id: Notion page ID
            status: Status value (e.g., "Success", "Error")
            error_message: Optional error message
        """
        try:
            properties = {
                "Status": {
                    "select": {
                        "name": status
                    }
                }
            }
            
            if error_message:
                properties["Error"] = {
                    "rich_text": [
                        {
                            "text": {
                                "content": error_message[:2000]  # Limit length
                            }
                        }
                    ]
                }
            
            response = requests.patch(
                f"{self.NOTION_API_BASE}/pages/{page_id}",
                headers=self.headers,
                json={"properties": properties},
                timeout=30
            )
            
            if response.status_code == 200:
                self.logger.info(f"Updated page {page_id} status to {status}")
            else:
                self.logger.warning(f"Failed to update page status: {response.status_code}")
                
        except Exception as e:
            self.logger.warning(f"Error updating page status: {e}")

