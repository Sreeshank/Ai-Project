import io
import os
import subprocess
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend import extraction

TSV = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n" + "5\t1\t1\t1\t1\t1\t10\t20\t100\t20\t95\tRegular\n5\t1\t1\t1\t1\t2\t120\t20\t40\t20\t90\tfee\n5\t1\t1\t1\t1\t3\t300\t20\t70\t20\t98\t2200\n"

class LowMemoryOCRTests(unittest.TestCase):
    def test_tesseract_uses_one_bounded_process_without_loading_neural_models(self):
        with patch.dict(os.environ,{'OCR_ENGINE':'tesseract'}), patch.object(extraction,'render_pdf_page',return_value=b'png') as render, patch('subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=TSV.encode())) as run, patch.object(extraction,'ocr_engine') as neural:
            page=extraction.ocr_page(b'pdf',1)
        self.assertEqual(page.method,'ocr');self.assertFalse(page.reviewed)
        self.assertIn('2200',page.text)
        self.assertIn('Tesseract',' '.join(page.warnings))
        self.assertLessEqual(render.call_args.kwargs['max_side'],1800)
        self.assertEqual(run.call_args.kwargs['env']['OMP_THREAD_LIMIT'],'1')
        self.assertLessEqual(run.call_args.kwargs['timeout'],90)
        self.assertNotIn('shell',run.call_args.kwargs)
        neural.assert_not_called()

    def test_timeout_and_missing_binary_fail_cleanly_without_neural_fallback(self):
        for error in (FileNotFoundError(),subprocess.TimeoutExpired('tesseract',90)):
            with self.subTest(error=type(error).__name__),patch.dict(os.environ,{'OCR_ENGINE':'tesseract'}),patch.object(extraction,'render_pdf_page',return_value=b'png'),patch('subprocess.run',side_effect=error),patch.object(extraction,'ocr_engine') as neural:
                with self.assertRaises(ValueError):extraction.ocr_page(b'pdf',1)
                neural.assert_not_called()

    def test_simultaneous_ingestion_is_rejected_and_lock_releases_on_errors(self):
        entered=threading.Event();release=threading.Event();errors=[]
        def slow(content,force_ocr=False):
            entered.set();release.wait(3);raise ValueError('Unreadable PDF')
        def first():
            try:extraction.extract_document(b'pdf','.pdf')
            except ValueError:pass
            except Exception as exc:errors.append(exc)
        with patch.object(extraction,'extract_pdf',side_effect=slow):
            worker=threading.Thread(target=first);worker.start()
            self.assertTrue(entered.wait(2))
            try:
                with self.assertRaises(extraction.ExtractionBusy):extraction.extract_document(b'text','.txt')
            finally:release.set();worker.join(4)
        self.assertFalse(errors)
        self.assertEqual(extraction.extract_document(b'Library opens at nine','.txt')[0][0].text,'Library opens at nine')
