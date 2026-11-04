"""Real scanned-PDF OCR and conservative memory check, run in the Linux image build."""
import io
import os
import shutil
import sys
import unittest
from unittest.mock import patch

@unittest.skipUnless(shutil.which('tesseract'), 'Tesseract is installed in the hosted Docker image')
class HostedOCRRuntimeTest(unittest.TestCase):
    def test_scanned_pdf_preserves_original_and_releases_worker_memory(self):
        # Include the API's imports in the parent-process memory measurement.
        from backend import main, extraction
        from PIL import Image, ImageDraw, ImageFont
        image=Image.new('RGB',(1200,1500),'white')
        draw=ImageDraw.Draw(image);font=ImageFont.load_default(size=34)
        draw.text((70,100),'Campus Library',fill='black',font=font)
        draw.text((70,180),'Regular examination fee 2200',fill='black',font=font)
        data=io.BytesIO();image.save(data,format='PDF',resolution=150);image.close()
        original=data.getvalue()
        with patch.dict(os.environ,{'OCR_ENGINE':'tesseract'}):
            pages,stored,media=extraction.extract_document(original,'.pdf')
            again,_,_=extraction.extract_document(original,'.pdf')
        self.assertEqual(media,'application/pdf');self.assertEqual(stored,original)
        self.assertEqual(pages[0].method,'ocr');self.assertFalse(pages[0].reviewed)
        self.assertIn('2200',pages[0].text);self.assertIn('Library',pages[0].text)
        self.assertIn('2200',again[0].text)
        self.assertNotIn('rapidocr',sys.modules)
        self.assertNotIn('onnxruntime',sys.modules)
        if sys.platform.startswith('linux'):
            import resource
            parent=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024
            child=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss/1024
            # Separate maxima sum to a conservative upper bound for this process tree.
            print(f'Hosted scan OCR peak memory upper bound: parent={parent:.1f} MiB; worker={child:.1f} MiB; combined={parent+child:.1f} MiB',flush=True)
            self.assertLess(parent+child,384,'Leave headroom under the 512 MiB container limit')
