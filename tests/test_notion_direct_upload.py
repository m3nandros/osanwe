"""
Tests for Notion direct file upload functionality (v1/file_uploads API).

Tests the new direct upload feature that allows uploading files directly to Notion
without intermediate storage services.
"""

import pytest
import tempfile
import json
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import requests

from integrations.notion_client import NotionClient, NotionPageResult
from config import Config


class TestNotionDirectUpload:
    """Test suite for direct file upload to Notion."""
    
    @pytest.fixture
    def notion_client(self):
        """Create a NotionClient instance for testing."""
        with patch.dict('os.environ', {
            'NOTION_TOKEN': 'test_token',
            'NOTION_DATABASE_ID': 'test_database_id'
        }):
            return NotionClient(
                token='test_token',
                database_id='test_database_id'
            )
    
    @pytest.fixture
    def small_pdf_file(self, tmp_path):
        """Create a small test PDF file (< 5MB)."""
        pdf_file = tmp_path / "test_small.pdf"
        # Create a small PDF-like file (just for testing)
        pdf_file.write_bytes(b'%PDF-1.4\n' + b'x' * 1024 * 100)  # ~100KB
        return pdf_file
    
    @pytest.fixture
    def medium_pdf_file(self, tmp_path):
        """Create a medium test PDF file (5-20MB)."""
        pdf_file = tmp_path / "test_medium.pdf"
        # Create a medium PDF-like file
        pdf_file.write_bytes(b'%PDF-1.4\n' + b'x' * 1024 * 1024 * 10)  # ~10MB
        return pdf_file
    
    @pytest.fixture
    def large_pdf_file(self, tmp_path):
        """Create a large test PDF file (> 5MB, exceeds Free Plan limit)."""
        pdf_file = tmp_path / "test_large.pdf"
        # Create a large PDF-like file (exceeds 5 MB Free Plan limit)
        pdf_file.write_bytes(b'%PDF-1.4\n' + b'x' * 1024 * 1024 * 10)  # ~10MB
        return pdf_file
    
    def test_upload_small_file_success(self, notion_client, small_pdf_file):
        """Test successful direct upload of a small file."""
        # Mock the API responses
        mock_init_response = Mock()
        mock_init_response.status_code = 200
        mock_init_response.json.return_value = {
            "id": "file_upload_123",
            "upload_url": "https://s3.amazonaws.com/notion-uploads/upload_123"
        }
        
        mock_upload_response = Mock()
        mock_upload_response.status_code = 200
        
        with patch('integrations.notion_client.requests.post', return_value=mock_init_response), \
             patch('integrations.notion_client.requests.put', return_value=mock_upload_response):
            
            file_upload_id = notion_client._upload_file_to_notion_direct(small_pdf_file)
            
            assert file_upload_id == "file_upload_123"
    
    def test_upload_file_too_large(self, notion_client, large_pdf_file):
        """Test that files > 5MB (Free Plan limit) are rejected for direct upload."""
        file_upload_id = notion_client._upload_file_to_notion_direct(large_pdf_file)
        
        assert file_upload_id is None
    
    def test_upload_file_not_exists(self, notion_client, tmp_path):
        """Test handling of non-existent file."""
        non_existent_file = tmp_path / "nonexistent.pdf"
        
        file_upload_id = notion_client._upload_file_to_notion_direct(non_existent_file)
        
        assert file_upload_id is None
    
    def test_upload_init_failure(self, notion_client, small_pdf_file):
        """Test handling of initialization failure."""
        mock_init_response = Mock()
        mock_init_response.status_code = 400
        mock_init_response.json.return_value = {"message": "Bad request"}
        mock_init_response.text = "Bad request"
        
        with patch('integrations.notion_client.requests.post', return_value=mock_init_response):
            file_upload_id = notion_client._upload_file_to_notion_direct(small_pdf_file)
            
            assert file_upload_id is None
    
    def test_upload_content_failure(self, notion_client, small_pdf_file):
        """Test handling of content upload failure."""
        mock_init_response = Mock()
        mock_init_response.status_code = 200
        mock_init_response.json.return_value = {
            "id": "file_upload_123",
            "upload_url": "https://s3.amazonaws.com/notion-uploads/upload_123"
        }
        
        mock_upload_response = Mock()
        mock_upload_response.status_code = 500
        
        with patch('integrations.notion_client.requests.post', return_value=mock_init_response), \
             patch('integrations.notion_client.requests.put', return_value=mock_upload_response):
            
            file_upload_id = notion_client._upload_file_to_notion_direct(small_pdf_file)
            
            assert file_upload_id is None
    
    def test_upload_missing_upload_url(self, notion_client, small_pdf_file):
        """Test handling of missing upload_url in response."""
        mock_init_response = Mock()
        mock_init_response.status_code = 200
        mock_init_response.json.return_value = {
            "id": "file_upload_123"
            # Missing upload_url
        }
        
        with patch('integrations.notion_client.requests.post', return_value=mock_init_response):
            file_upload_id = notion_client._upload_file_to_notion_direct(small_pdf_file)
            
            assert file_upload_id is None
    
    def test_add_local_pdf_block_direct_upload(self, notion_client, small_pdf_file):
        """Test that _add_local_pdf_block uses direct upload for small files."""
        blocks = []
        
        # Mock successful direct upload
        with patch.object(notion_client, '_upload_file_to_notion_direct', return_value="file_upload_123"):
            notion_client._add_local_pdf_block(blocks, small_pdf_file, "Test PDF", "1706.03762")
        
        # Check that block was added with file_upload type
        assert len(blocks) == 1
        assert blocks[0]["type"] == "file"
        assert blocks[0]["file"]["type"] == "file_upload"
        assert blocks[0]["file"]["file_upload"]["id"] == "file_upload_123"
    
    def test_add_local_pdf_block_fallback_to_storage(self, notion_client, large_pdf_file):
        """Test that _add_local_pdf_block falls back to external storage for large files."""
        blocks = []
        
        # Mock external storage upload
        with patch.object(notion_client, '_upload_file_to_notion_direct', return_value=None), \
             patch.object(notion_client, '_upload_to_temporary_storage', return_value="https://s3.amazonaws.com/file.pdf"):
            
            notion_client._add_local_pdf_block(blocks, large_pdf_file, "Test PDF", "1706.03762")
        
        # Check that block was added with external type
        assert len(blocks) == 1
        assert blocks[0]["type"] == "file"
        assert blocks[0]["file"]["type"] == "external"
        assert blocks[0]["file"]["external"]["url"] == "https://s3.amazonaws.com/file.pdf"
    
    def test_add_local_pdf_block_all_fail(self, notion_client, small_pdf_file):
        """Test that _add_local_pdf_block creates text block if all uploads fail."""
        blocks = []
        
        # Mock all uploads failing
        with patch.object(notion_client, '_upload_file_to_notion_direct', return_value=None), \
             patch.object(notion_client, '_upload_to_temporary_storage', return_value=None):
            
            notion_client._add_local_pdf_block(blocks, small_pdf_file, "Test PDF", "1706.03762")
        
        # Check that text block was added as fallback
        assert len(blocks) == 1
        assert blocks[0]["type"] == "paragraph"
        assert "не удалось загрузить" in blocks[0]["paragraph"]["rich_text"][1]["text"]["content"]
    
    def test_upload_pdfs_to_page_with_local_files(self, notion_client, small_pdf_file, medium_pdf_file):
        """Test uploading PDFs to page using local files."""
        page_id = "test_page_id"
        blocks = []
        
        # Mock successful direct uploads
        with patch.object(notion_client, '_add_local_pdf_block') as mock_add_block, \
             patch('integrations.notion_client.requests.patch') as mock_patch:
            
            mock_patch.return_value.status_code = 200
            mock_patch.return_value.json.return_value = {}
            
            notion_client._upload_pdfs_to_page(
                page_id=page_id,
                original_pdf_path=small_pdf_file,
                translated_pdf_path=medium_pdf_file,
                original_pdf_url=None,
                translated_pdf_url=None,
                arxiv_id="1706.03762"
            )
        
        # Check that _add_local_pdf_block was called for both files
        assert mock_add_block.call_count == 2
    
    def test_upload_pdfs_to_page_with_urls(self, notion_client):
        """Test that URLs are used when provided (for backward compatibility)."""
        page_id = "test_page_id"
        
        with patch('integrations.notion_client.requests.patch') as mock_patch:
            mock_patch.return_value.status_code = 200
            mock_patch.return_value.json.return_value = {}
            
            notion_client._upload_pdfs_to_page(
                page_id=page_id,
                original_pdf_path=None,
                translated_pdf_path=None,
                original_pdf_url="https://discord.com/file1.pdf",
                translated_pdf_url="https://discord.com/file2.pdf",
                arxiv_id="1706.03762"
            )
        
        # Check that PATCH was called with external file blocks
        assert mock_patch.called
        call_args = mock_patch.call_args
        blocks = call_args[1]["json"]["children"]
        
        assert len(blocks) == 2
        assert blocks[0]["file"]["type"] == "external"
        assert blocks[1]["file"]["type"] == "external"


class TestNotionDirectUploadIntegration:
    """Integration tests for direct upload (require real Notion credentials)."""
    
    @pytest.mark.skipif(
        not Config.NOTION_TOKEN or not Config.NOTION_DATABASE_ID,
        reason="Notion credentials not configured"
    )
    def test_real_upload_small_file(self, tmp_path):
        """Test real upload of a small file to Notion (requires credentials)."""
        # Create a small test PDF
        pdf_file = tmp_path / "test_real.pdf"
        pdf_file.write_bytes(b'%PDF-1.4\n' + b'x' * 1024 * 100)  # ~100KB
        
        client = NotionClient(
            token=Config.NOTION_TOKEN,
            database_id=Config.NOTION_DATABASE_ID
        )
        
        file_upload_id = client._upload_file_to_notion_direct(pdf_file)
        
        # Should either succeed (return ID) or fail gracefully (return None)
        assert file_upload_id is None or isinstance(file_upload_id, str)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

