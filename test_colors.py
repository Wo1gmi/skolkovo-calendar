import unittest
from sync import event_color, SUBJECT_COLORS, NORMAL_COLORS

class ColorsTest(unittest.TestCase):
    def test_dictionary_aliases(self):
        for entry in SUBJECT_COLORS:
            for alias in entry['aliases']:
                self.assertEqual(event_color({'title': alias}), entry['color_id'])

    def test_priority(self):
        self.assertEqual(event_color({'title': 'Микроэкономика гр.1'}), '8')
        self.assertEqual(event_color({'title': 'Микроэкономика гр.1 и гр.2'}), '7')
        for word in ('Тест', 'Контрольная', 'Итоговое занятие', 'Экзамен', 'Зачёт', 'Final exam'):
            self.assertEqual(event_color({'title': 'Микроэкономика гр.1', 'note': word}), '8')
            self.assertEqual(event_color({'title': 'Микроэкономика гр.1 и гр.2', 'note': word}), '11')
            self.assertEqual(event_color({'title': 'Микроэкономика гр.2', 'note': word}), '11')

    def test_unknown_stable_allowed(self):
        item = {'title': 'Новый предмет'}
        self.assertIn(event_color(item), NORMAL_COLORS)
        self.assertEqual(event_color(item), event_color(dict(item)))

if __name__ == '__main__':
    unittest.main()
