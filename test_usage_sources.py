"""Offline regression checks; no CLI, authentication, or network requests."""
import unittest

from usage_sources import (
    UsageError, claude_complete, claude_render_complete, parse_claude, parse_codex,
)


class UsageParsingTests(unittest.TestCase):
    def test_current_claude_format_and_model_boundary(self):
        rows = parse_claude('''Current session
  ███ 8% used
  Resets 2pm (Asia/Taipei)
Current week (all models)
  ███ 13% used
  Resets Sep 10, 3:30pm (Asia/Taipei)
Current week (Fable)
  ███ 17% used
Usage credits
Usage credits are off · /usage-credits to turn them on
''')
        self.assertTrue(claude_complete(rows))
        self.assertEqual([r.used for r in rows], [8, 13, 17, None])
        self.assertIn('Sep 10, 3:30pm', rows[1].resets)
        self.assertEqual(rows[-1].note, '未啟用')

    def test_missing_all_model_value_does_not_borrow_next_model(self):
        rows = parse_claude('Current session\n8% used\nCurrent week (all models)\n'
                            'Current week (Fable)\n17% used\n')
        self.assertIsNone(rows[1].used)
        self.assertFalse(claude_complete(rows))

    def test_empty_or_loading_is_not_zero(self):
        self.assertFalse(claude_complete(parse_claude('Loading usage...')))
        self.assertFalse(claude_complete(parse_claude('Current session\n')))

    def test_claude_requires_end_marker_before_commit(self):
        partial = 'Current session\n8% used\nCurrent week (all models)\n13% used\n'
        rows = parse_claude(partial)
        self.assertTrue(claude_complete(rows))
        self.assertFalse(claude_render_complete(partial, rows))
        complete = partial + 'Usage credits\nUsage credits are off\nEsc to cancel\n'
        self.assertTrue(claude_render_complete(complete, parse_claude(complete)))

    def test_claude_percentage_semantics(self):
        self.assertEqual(parse_claude('Current session\n80% left')[0].used, 20)
        self.assertEqual(parse_claude('Current session\n0% used')[0].used, 0)
        self.assertEqual(parse_claude('Current session\n100% used')[0].used, 100)

    def test_codex_used_is_not_inverted(self):
        rows = parse_codex({'rateLimits': {
            'primary': {'usedPercent': 78, 'windowDurationMins': 300},
            'secondary': {'usedPercent': 88, 'windowDurationMins': 10080}}})
        self.assertEqual([r.used for r in rows], [78, 88])
        self.assertEqual([r.label for r in rows], ['5 小時額度', '每週額度'])

    def test_codex_multiple_buckets_and_missing_window(self):
        rows = parse_codex({'rateLimitsByLimitId': {
            'codex': {'primary': None, 'secondary': {'usedPercent': 0, 'windowDurationMins': 10080}},
            'other': {'limitName': 'Other', 'primary': {'usedPercent': 100, 'windowDurationMins': 15}}},
            'rateLimits': {'primary': {'usedPercent': 99}}})
        self.assertEqual([r.used for r in rows], [0, 100])
        self.assertIn('Other', rows[1].label)

    def test_invalid_percentages_fail_closed(self):
        for value in (None, True, '50', -1, 101, float('nan')):
            with self.subTest(value=value), self.assertRaises(UsageError):
                parse_codex({'rateLimits': {'primary': {'usedPercent': value}}})
        with self.assertRaises(UsageError):
            parse_claude('Current session\n101% used')

    def test_codex_no_limits_is_not_zero(self):
        for value in ({}, {'rateLimits': None}, {'rateLimits': {'primary': None}}):
            with self.subTest(value=value), self.assertRaises(UsageError):
                parse_codex(value)


if __name__ == '__main__':
    unittest.main()
