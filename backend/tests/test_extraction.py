import unittest
from backend.extraction import ocr_to_markdown, search_chunks


def cell(text,x,y,width=60):
    return {'text': text, 'score': .99, 'box': [[x,y],[x+width,y],[x+width,y+15],[x,y+15]]}


class ExtractionTest(unittest.TestCase):
    def test_tables_keep_wrapped_cells_and_rows(self):
        records = [cell('S. No.',0,0),cell('Regulation',100,0),cell('Semester',230,0),
            cell('1',0,30),cell('URR24',100,30),cell('III',220,30),cell('(2025 batch)',210,50),
            cell('2',0,80),cell('URR18',100,80),cell('VII',220,80)]
        text=ocr_to_markdown(records)
        self.assertIn('| 1 | URR24 | III (2025 batch) |',text)
        self.assertIn('| 2 | URR18 | VII |',text)

    def test_amounts_and_dates_are_not_guessed_or_rewritten(self):
        records=[cell('Regular fee',0,0),cell('2,200.00',250,0),
            cell('Without late fee',0,35),cell('29.10.2026',250,35)]
        text=ocr_to_markdown(records)
        self.assertIn('Regular fee: 2,200.00',text)
        self.assertIn('Without late fee: 29.10.2026',text)
        self.assertNotIn('12,200',text)

    def test_long_lines_still_create_bounded_search_passages(self):
        text='word ' * 3000
        chunks=search_chunks(text)
        self.assertGreater(len(chunks),1)
        self.assertTrue(all(len(chunk)<=1800 for chunk in chunks))

    def test_low_confidence_line_is_not_replaced_with_empty_recognition(self):
        import io
        from types import SimpleNamespace
        from unittest.mock import patch
        from PIL import Image
        from backend.extraction import refine_long_lines
        image=Image.new('RGB',(1000,20),'white')
        buffer=io.BytesIO(); image.save(buffer,format='PNG'); image.close()
        record={'text':'Original readable text','score':.4,'box':[[0,0],[1000,0],[1000,20],[0,20]]}
        with patch('backend.extraction.ocr_engine') as engine:
            engine.return_value.return_value=SimpleNamespace(txts=('',),scores=[1.0])
            result=refine_long_lines(buffer.getvalue(),[record])
        self.assertEqual(result[0]['text'],'Original readable text')


if __name__ == '__main__':
    unittest.main()
