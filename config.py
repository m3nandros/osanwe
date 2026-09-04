"""
Configuration management for Rosetta v2.

Handles loading and validation of environment variables and application settings.
"""

import os
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

# Load environment variables from .env file, but don't fail if it's not accessible
try:
    load_dotenv()
except PermissionError as e:
    print(f"⚠️  Warning: unable to read .env file ({e}). Continuing with existing environment.")

# Project paths
BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"
TEMP_DIR = BASE_DIR / "temp"
LOGS_DIR = BASE_DIR / "logs"
GLOSSARY_PATH = BASE_DIR / "glossary.json"
FALLBACK_KEY_FILE = Path(os.getenv("ROSETTA_OPENAI_KEY_FILE", BASE_DIR / ".openai_key"))

# Ensure directories exist
OUTPUT_DIR.mkdir(exist_ok=True)
TEMP_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)


class Config:
    """
    Configuration class for Rosetta v2.
    
    Loads settings from environment variables and provides access to configuration
    values throughout the application.
    """
    
    # OpenAI API
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    OPENAI_TEMPERATURE: float = float(os.getenv("OPENAI_TEMPERATURE", "0.3"))
    OPENAI_MAX_TOKENS: str = os.getenv("OPENAI_MAX_TOKENS", "auto")
    
    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    
    # Directories
    OUTPUT_DIR: Path = Path(os.getenv("OUTPUT_DIR", str(OUTPUT_DIR)))
    TEMP_DIR: Path = Path(os.getenv("TEMP_DIR", str(TEMP_DIR)))
    LOGS_DIR: Path = Path(os.getenv("LOGS_DIR", str(LOGS_DIR)))
    
    # arXiv API
    ARXIV_API_URL: str = "http://export.arxiv.org/api/query"
    
    # LaTeX compilation
    PDFLATEX_COMMAND: str = "pdflatex"
    PDFLATEX_TIMEOUT: int = 300  # 5 minutes for large documents
    
    # Glossary
    GLOSSARY_PATH: Path = Path(os.getenv("GLOSSARY_PATH", str(GLOSSARY_PATH)))
    
    # Notion API
    NOTION_TOKEN: str = os.getenv("NOTION_TOKEN", "")
    NOTION_DATABASE_ID: str = os.getenv("NOTION_DATABASE_ID", "")
    
    # File upload strategy for Notion
    # Options: "auto" (try Discord URL, then temporary storage), "temporary" (always use temporary storage), "discord_only" (only Discord URLs), "private" (use private storage only)
    NOTION_FILE_UPLOAD_STRATEGY: str = os.getenv("NOTION_FILE_UPLOAD_STRATEGY", "auto")
    
    # Storage service preference
    # Options: "fileio", "transfersh", "0x0", "auto" (try all in order), "s3" (AWS S3), "s3_public" (AWS S3 with public URLs), "local" (local storage only, no public URL), "permanent" (permanent local storage with web server), "notion_direct" (direct upload to Notion, files ≤ 5MB)
    NOTION_TEMP_STORAGE: str = os.getenv("NOTION_TEMP_STORAGE", "auto")
    
    # Notion direct upload limit (MB)
    # Free Plan: 5 MB, Paid Plans: up to 5 GB
    # We use 5 MB to ensure compatibility with Free Plan
    NOTION_DIRECT_UPLOAD_LIMIT_MB: float = float(os.getenv("NOTION_DIRECT_UPLOAD_LIMIT_MB", "5.0"))
    
    # AWS S3 Configuration (for file storage)
    AWS_ACCESS_KEY_ID: str = os.getenv("AWS_ACCESS_KEY_ID", "")
    AWS_SECRET_ACCESS_KEY: str = os.getenv("AWS_SECRET_ACCESS_KEY", "")
    AWS_S3_BUCKET: str = os.getenv("AWS_S3_BUCKET", "")
    AWS_S3_REGION: str = os.getenv("AWS_S3_REGION", "us-east-1")
    AWS_S3_PRESIGNED_URL_EXPIRY: int = int(os.getenv("AWS_S3_PRESIGNED_URL_EXPIRY", "604800"))  # 7 days default
    AWS_S3_PUBLIC_ACCESS: bool = os.getenv("AWS_S3_PUBLIC_ACCESS", "false").lower() == "true"  # Use public URLs instead of presigned
    
    # Permanent local storage configuration
    PERMANENT_STORAGE_DIR: Path = Path(os.getenv("PERMANENT_STORAGE_DIR", str(BASE_DIR / "permanent_storage")))
    PERMANENT_STORAGE_URL_BASE: str = os.getenv("PERMANENT_STORAGE_URL_BASE", "")  # Base URL for accessing files (e.g., https://files.yourdomain.com)
    
    # Notion Database Property Names (customize to match your database schema)
    NOTION_PROP_TITLE: str = os.getenv("NOTION_PROP_TITLE", "Название статьи")  # First property (Title type)
    NOTION_PROP_AUTHORS: str = os.getenv("NOTION_PROP_AUTHORS", "Авторы")
    NOTION_PROP_DATE: str = os.getenv("NOTION_PROP_DATE", "Дата добавления")
    NOTION_PROP_SUMMARY: str = os.getenv("NOTION_PROP_SUMMARY", "Резюме")
    NOTION_PROP_LINK: str = os.getenv("NOTION_PROP_LINK", "Ссылка")
    NOTION_PROP_STATUS: str = os.getenv("NOTION_PROP_STATUS", "Статус чтения")
    NOTION_PROP_TYPE: str = os.getenv("NOTION_PROP_TYPE", "Вид")
    NOTION_PROP_TOPIC: str = os.getenv("NOTION_PROP_TOPIC", "Тема")
    
    # Notion Default Values
    NOTION_DEFAULT_TYPE: str = os.getenv("NOTION_DEFAULT_TYPE", "Статья")  # Default value for "Вид"
    NOTION_DEFAULT_STATUS: str = os.getenv("NOTION_DEFAULT_STATUS", "Не прочитано")  # Default value for "Статус чтения"
    
    @classmethod
    def validate(cls) -> bool:
        """
        Validate that all required configuration is present.
        
        Returns:
            bool: True if configuration is valid, False otherwise.
        """
        required_vars = [
            ("OPENAI_API_KEY", cls.ensure_api_key()),
        ]
        
        missing = []
        for var_name, var_value in required_vars:
            if not var_value:
                missing.append(var_name)
        
        if missing:
            print(f"❌ Missing required environment variables: {', '.join(missing)}")
            print(f"Please create a .env file with the required variables.")
            print(f"See .env.example for reference.")
            return False
        
        # Validate directories
        try:
            cls.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            cls.TEMP_DIR.mkdir(parents=True, exist_ok=True)
            cls.LOGS_DIR.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            print(f"❌ Error creating directories: {e}")
            return False
        
        # Validate glossary file exists
        if not cls.GLOSSARY_PATH.exists():
            print(f"⚠️  Warning: Glossary file not found at {cls.GLOSSARY_PATH}")
            print(f"Creating empty glossary file...")
            try:
                cls.GLOSSARY_PATH.parent.mkdir(parents=True, exist_ok=True)
                cls.GLOSSARY_PATH.write_text("{}", encoding="utf-8")
            except Exception as e:
                print(f"❌ Error creating glossary file: {e}")
                return False
        
        return True
    
    @classmethod
    def get_openai_config(cls) -> dict:
        """
        Get OpenAI API configuration as a dictionary.
        
        Returns:
            dict: OpenAI configuration parameters
        """
        api_key = cls.ensure_api_key()
        return {
            "api_key": api_key,
            "model": cls.OPENAI_MODEL,
            "temperature": cls.OPENAI_TEMPERATURE,
            "max_tokens": cls.OPENAI_MAX_TOKENS,
        }

    @classmethod
    def ensure_api_key(cls) -> str:
        """
        Ensure OPENAI_API_KEY is available by falling back to .openai_key file if needed.
        """
        if cls.OPENAI_API_KEY:
            return cls.OPENAI_API_KEY
        fallback = cls._load_key_from_file()
        if fallback:
            cls.OPENAI_API_KEY = fallback
            os.environ["OPENAI_API_KEY"] = fallback
        return cls.OPENAI_API_KEY

    @classmethod
    def _load_key_from_file(cls) -> str:
        key_path = FALLBACK_KEY_FILE
        try:
            if key_path.exists():
                key = key_path.read_text(encoding="utf-8").strip()
                if key:
                    print(f"ℹ️  Loaded OPENAI_API_KEY from {key_path}")
                    return key
        except PermissionError as e:
            print(f"⚠️  Unable to read fallback OpenAI key file ({e}).")
        return ""


# Create global config instance
config = Config()

# Validate configuration on module import (optional, can be done explicitly)
# Uncomment if you want automatic validation on import
# if not Config.validate():
#     raise ValueError("Configuration validation failed. Please check your .env file.")

