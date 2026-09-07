import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import mugen_char_linter as linter


class ParsingTests(unittest.TestCase):
    def test_remove_tagged_lines_deletes_only_linter_tagged_lines(self):
        content = (
            '; [CNS Unknown Parameter] mystery = 1\n'
            '; [AIR Garbage Line] stray text\n'
            '; [User Note] keep this\n'
        )
        output, count = linter.remove_tagged_lines(content)
        self.assertEqual(
            output,
            '; [User Note] keep this\n',
        )
        self.assertEqual(count, 2)

    def test_strip_comment_preserves_semicolon_in_quotes(self):
        self.assertEqual(
            linter.strip_comment('text = "a;b" ; comment'),
            'text = "a;b"',
        )

    def test_split_key_value_ignores_nested_equals(self):
        self.assertEqual(
            linter.split_key_value('value = ifelse(var(0) = 1, 2, 3)'),
            ('value', 'ifelse(var(0) = 1, 2, 3)'),
        )

    def test_garbage_requires_top_level_equals(self):
        self.assertTrue(linter.is_garbage_line('stray text'))
        self.assertFalse(linter.is_garbage_line('[Unknown Section]'))
        self.assertFalse(linter.is_garbage_line('value = ifelse(a = b, 1, 0)'))

    def test_state_header_rewrite_preserves_comment_spacing(self):
        result, changed = linter.rewrite_state_header(
            '  [state 99,  foo]   ; keep this spacing', '10'
        )
        self.assertTrue(changed)
        self.assertEqual(result, '  [State 10, foo]   ; keep this spacing')


class CnsProcessingTests(unittest.TestCase):
    def test_prune_dedupe_and_invalid_value_in_tag_mode(self):
        content = (
            '[Statedef 1]\n'
            'type = S\n'
            '[State 99]\n'
            'type = Trans\n'
            'trans = bogus\n'
            'alpha = 1\n'
            'alpha = 2\n'
            'not_a_real_key = 3\n'
        )
        output, log, stats = linter.process_file(
            content, True, True, True, False, 'tag', True
        )
        self.assertIn('; [CNS Invalid Value] trans = bogus', output)
        self.assertIn('; [CNS Duplicate Parameter] alpha = 2', output)
        self.assertIn('; [CNS Unknown Parameter] not_a_real_key = 3', output)
        self.assertIn('[State 1]', output)
        self.assertEqual(stats['invalid_values_removed'], 1)
        self.assertEqual(stats['duplicates_removed'], 1)
        self.assertEqual(stats['pruned'], 1)
        self.assertEqual(len(log), 3)

    def test_garbage_can_be_deleted_without_affecting_headers(self):
        content = '[Statedef 0]\nstray\n[State 0]\nvalue = 1\n'
        output, _, stats = linter.process_file(
            content, False, False, False, True, 'delete'
        )
        self.assertNotIn('stray', output)
        self.assertIn('[State 0]', output)
        self.assertEqual(stats['garbage_lines_handled'], 1)

    def test_invalid_removal_mode_is_runtime_error(self):
        with self.assertRaises(ValueError):
            linter.process_file('', False, False, False, False, 'invalid')


class CmdAndAirTests(unittest.TestCase):
    def test_cmd_dedupe_is_scoped_to_each_block(self):
        content = (
            '[Command]\nname = a\nname = b\n\n'
            '[Command]\nname = a\ncommand = ~D, DF_a\n'
        )
        output, _, stats = linter.process_cmd_file(content, False, True, 'comment')
        self.assertIn('; name = b', output)
        self.assertIn('[Command]\nname = a\ncommand = ~D, DF_a', output)
        self.assertEqual(stats['cmd_duplicates_removed'], 1)

    def test_air_duplicate_action_comments_header_and_body(self):
        content = (
            '[Begin Action 1]\n'
            '1,0,0,0,1\n'
            '[Begin Action 1]\n'
            '2,0,0,0,1\n'
        )
        output, _, stats = linter.process_air_file(
            content, True, False, False, False, 'comment'
        )
        self.assertIn('; [Begin Action 1]', output)
        self.assertIn('; 2,0,0,0,1', output)
        self.assertEqual(stats['duplicate_actions_removed'], 1)

    def test_air_fallthrough_is_baked(self):
        content = '[Begin Action 1]\n[Begin Action 2]\n1,0,0,0,1\n'
        output, _, stats = linter.process_air_file(
            content, False, True, True, False, 'comment'
        )
        self.assertIn('[Begin Action 1]\n1,0,0,0,1', output)
        self.assertEqual(stats['empty_actions_baked'], 1)
        self.assertEqual(stats['empty_actions_flagged'], 0)


class FileOperationTests(unittest.TestCase):
    def test_ikemen_detection_requires_key_in_info_section(self):
        with tempfile.TemporaryDirectory() as directory:
            def_path = Path(directory) / 'fighter.def'
            def_path.write_text(
                '[Files]\nikemenversion = 1.0\n'
                '[iNfO]\nIkemenVersion = 1.0 ; engine version\n',
                encoding='utf-8',
            )
            self.assertTrue(linter.is_ikemen_def(str(def_path)))

            def_path.write_text(
                '[Files]\nikemenversion = 1.0\n'
                '[Info]\n; ikemenversion = 1.0\nname = Fighter\n',
                encoding='utf-8',
            )
            self.assertFalse(linter.is_ikemen_def(str(def_path)))

    def test_def_discovery_orders_state_files_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def_path = root / 'fighter.def'
            def_path.write_text(
                '[Files]\n'
                'st1 = one.cns\n'
                'st = base.cns\n'
                'st0 = zero.cns\n'
                'stcommon = shared.cns\n'
                'cmd = commands.cmd\n'
                'anim = moves.air\n',
                encoding='utf-8',
            )
            expected = [
                str(root / 'base.cns'),
                str(root / 'zero.cns'),
                str(root / 'one.cns'),
                str(root / 'commands.cmd'),
            ]
            self.assertEqual(linter.discover_state_files_from_def(str(def_path)), expected)
            self.assertEqual(
                linter.discover_air_file_from_def(str(def_path)),
                str(root / 'moves.air'),
            )

    def test_run_cns_can_remove_tagged_lines_without_other_fixes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'input.cns'
            path.write_text('; [CNS Unknown Parameter] mystery = 1\n', encoding='utf-8')
            log, stats, _, _, wrote = linter.run_cns_file(
                str(path), str(path), False, False, False, False, False, False,
                'comment', False, do_remove_tagged_lines=True,
            )
            self.assertTrue(wrote)
            self.assertEqual(path.read_text(encoding='utf-8'), '')
            self.assertEqual(log, [])
            self.assertEqual(stats['tagged_lines_removed'], 1)
            totals = {
                'pruned': 0, 'duplicates_removed': 0, 'invalid_values_removed': 0,
                'headers_normalized': 0, 'cmd_pruned': 0,
                'cmd_duplicates_removed': 0, 'garbage_lines_handled': 0,
                'air_garbage_lines_handled': 0, 'tagged_lines_removed': 1,
            }
            summary = linter.render_batch_summary(
                totals=totals, has_cns=True, has_air=False, diffs_written=0,
                processed=1, failed=0, dry_run=False, multi=False, do_diff=False,
                removal_mode='comment',
            )
            self.assertTrue(any('Tagged lines removed:' in line and line.endswith('1')
                                for line in summary))
            self.assertEqual(summary[-1], '  All tasks ran successfully.')

            failed_summary = linter.render_batch_summary(
                totals=totals, has_cns=True, has_air=False, diffs_written=0,
                processed=0, failed=1, dry_run=False, multi=False, do_diff=False,
                removal_mode='comment',
            )
            self.assertEqual(failed_summary[-1], '  Some tasks failed; see errors above.')

    def test_commit_output_does_not_rewrite_unchanged_content(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'input.cns'
            path.write_text('clean\n', encoding='utf-8')
            source = linter.read_text_file(str(path))
            result = linter.commit_output(
                str(path), str(path), source, 'clean\n', True, True, False
            )
            self.assertEqual(result, (None, None, False))
            self.assertFalse((Path(str(path) + '.bak')).exists())



def run_cns(path, output=None, prune=True, backup=True):
    return linter.run_cns_file(
        str(path), str(output or path), prune, True, True, False, False, False,
        'comment', backup,
    )


class FileFidelityTests(unittest.TestCase):
    def test_non_utf8_bytes_line_endings_and_bom_survive(self):
        # Shift-JIS comment whose trail byte is 0x85 (a line break for splitlines).
        comment = b'; \x83L\x83\x85\x83\x89 comment'
        for bom in (b'', b'\xef\xbb\xbf'):
            with self.subTest(bom=bom), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'input.cns'
                # Flagged line ends in a kanji whose trail byte is 0xA0.
                original = (bom + comment + b'\r\n[Statedef 0]\r\ntype = S\r\n'
                            b'bogus = 1 \x88\xa0\r\n')
                path.write_bytes(original)
                log, stats, backup, _, wrote = run_cns(path)
                self.assertTrue(wrote)
                self.assertEqual(Path(backup).read_bytes(), original)
                self.assertEqual(path.read_bytes(), original.replace(b'bogus', b'; bogus'))
                report = linter.render_file_report(str(path), log, stats, wrote, backup,
                                                   None, False, str(path))
                '\n'.join(report).encode('utf-8')  # must not contain lone surrogates

    def test_missing_final_newline_is_not_a_change(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'input.cns'
            path.write_bytes(b'[Statedef 0]\ntype = S')
            self.assertFalse(run_cns(path)[4])
            path.write_bytes(b'[Statedef 0]\nbogus = 1')
            self.assertTrue(run_cns(path)[4])
            self.assertEqual(path.read_bytes(), b'[Statedef 0]\n; bogus = 1')

    def test_output_folder_equal_to_source_still_backs_up(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'input.cns'
            path.write_text('[Statedef 0]\nbogus = 1\n', encoding='utf-8')
            other_spelling = os.path.join(directory, '.', 'input.cns')  # pathlib would collapse '.'
            _, _, backup, _, wrote = run_cns(path, other_spelling)
            self.assertTrue(wrote)
            self.assertIsNotNone(backup)

    def test_output_paths_never_collide(self):
        inputs = [os.path.join('a', 'kfm', 'common.cmd'), os.path.join('b', 'kfm', 'common.cmd'),
                  os.path.join('c', 'ryu', 'common.cmd'), os.path.join('c', 'ryu', 'ryu.air')]
        planned = linter.plan_output_paths(inputs, 'out')
        self.assertEqual(len(set(planned.values())), 4)
        self.assertEqual(planned[inputs[3]], os.path.join('out', 'ryu.air'))
        self.assertEqual(planned[inputs[2]], os.path.join('out', 'ryu', 'common.cmd'))
        self.assertEqual(linter.plan_output_paths(inputs, None)[inputs[0]], inputs[0])

    def test_same_file_listed_twice_is_processed_once(self):
        paths = ['x.cns', os.path.join('.', 'x.cns'), 'y.cns']
        self.assertEqual(linter.dedupe_paths(paths), ['x.cns', 'y.cns'])


class BlockBoundaryTests(unittest.TestCase):
    def test_non_state_section_ends_state_block(self):
        content = (
            '[Statedef -1]\n\n[State -1, x]\ntype = ChangeState\nvalue = 200\n\n'
            '[Command]\nname = "QCF"\ncommand = ~D, DF, F\ntime = 15\n'
        )
        output, log, _ = linter.process_file(content, True, True, True, True, 'comment')
        self.assertEqual(log, [])
        self.assertEqual(output, content)


class CliTests(unittest.TestCase):
    def test_prompts_take_default_without_console(self):
        with mock.patch('builtins.input', side_effect=EOFError), \
                mock.patch('builtins.print'):
            self.assertTrue(linter.ask_yes_no('?', default=True))
            self.assertFalse(linter.ask_yes_no('?', default=False))
            self.assertEqual(linter.ask_choice('?', {'a': 'A', 'b': 'B'}, 'b'), 'b')

    def test_unreadable_def_is_reported_not_raised(self):
        with mock.patch('builtins.print'):
            files, _, failed = linter.expand_inputs(['does-not-exist.def'])
        self.assertEqual((files, failed), ([], 1))


if __name__ == '__main__':
    unittest.main()
