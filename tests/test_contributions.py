import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import update_contributions as updater


def pull_request(repo='someone/project', number=1, **overrides):
    value = {
        'number': number,
        'title': f'Fix something useful {number}',
        'html_url': f'https://github.com/{repo}/pull/{number}',
        'merged': True,
        'merged_at': '2026-09-13T06:25:57Z',
        'user': {'login': 'VeraPyuyi'},
        'base': {'repo': {'full_name': repo, 'private': False}},
    }
    value.update(overrides)
    return value


class FakeAPI:
    def __init__(self, prs):
        self.prs = copy.deepcopy(prs)
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        if '/search/issues?' in url:
            page = int(parse_qs(urlparse(url).query)['page'][0])
            items = [{
                'number': pr['number'],
                'html_url': pr['html_url'],
                'repository_url': 'https://api.github.com/repos/' + pr['base']['repo']['full_name'],
            } for pr in self.prs]
            return {'total_count': len(items), 'incomplete_results': False,
                    'items': items[(page - 1) * 100:page * 100]}
        for pr in self.prs:
            if url == f"https://api.github.com/repos/{pr['base']['repo']['full_name']}/pulls/{pr['number']}":
                return copy.deepcopy(pr)
        raise AssertionError(f'Unexpected request: {url}')


class ContributionTests(unittest.TestCase):
    def test_only_verified_public_external_authored_prs_count(self):
        prs = [
            pull_request('VeraPyuyi/own'),
            pull_request('alice/public', 2, merged_at='2026-07-01T10:02:12Z'),
            pull_request('bob/public', 3),
            pull_request('carol/public', 4, user={'login': 'someone-else'}),
            pull_request('dave/private', 5, base={'repo': {'full_name': 'dave/private', 'private': True}}),
        ]
        api = FakeAPI(prs)
        result = updater.fetch_contributions(api)
        self.assertEqual([pr['number'] for pr in result], [3, 2])
        self.assertFalse(any('/repos/VeraPyuyi/own/pulls/' in url for url in api.calls))
        self.assertNotIn('private', json.dumps(result))

    def test_pagination_collects_all_results(self):
        api = FakeAPI([pull_request(number=number) for number in range(1, 102)])
        self.assertEqual(len(updater.fetch_contributions(api)), 101)
        self.assertEqual(sum('/search/issues?' in url for url in api.calls), 2)

    def test_search_deduplicates_urls(self):
        item = {'html_url': 'https://github.com/alice/public/pull/1'}
        result = updater.search_items(lambda _: {
            'total_count': 1, 'incomplete_results': False, 'items': [item, item]})
        self.assertEqual(result, [item])

    def test_incomplete_search_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            updater.search_items(lambda _: {'total_count': 1, 'incomplete_results': True, 'items': []})

    def test_missing_page_results_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            updater.search_items(lambda _: {'total_count': 2, 'incomplete_results': False, 'items': [
                {'html_url': 'https://github.com/alice/public/pull/1'}]})

    def test_changing_search_total_is_rejected(self):
        pages = iter([
            {'total_count': 101, 'incomplete_results': False, 'items': [
                {'html_url': f'https://github.com/alice/public/pull/{number}'} for number in range(1, 101)]},
            {'total_count': 102, 'incomplete_results': False, 'items': []},
        ])
        with self.assertRaisesRegex(ValueError, 'changed'):
            updater.search_items(lambda _: next(pages))

    def test_search_limit_is_not_silently_truncated(self):
        with self.assertRaisesRegex(ValueError, '1,000'):
            updater.search_items(lambda _: {'total_count': 1001, 'incomplete_results': False, 'items': []})

    def test_unmerged_pr_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'merged'):
            updater.fetch_contributions(FakeAPI([pull_request(merged=False)]))

    def test_unverified_visibility_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'visibility'):
            updater.fetch_contributions(FakeAPI([pull_request(
                base={'repo': {'full_name': 'someone/project'}})]))

    def test_merge_timestamp_must_have_timezone(self):
        with self.assertRaisesRegex(ValueError, 'timezone'):
            updater.fetch_contributions(FakeAPI([pull_request(merged_at='2026-09-13T06:25:57')]))

    def test_timezone_normalizes_to_utc(self):
        result = updater.fetch_contributions(FakeAPI([pull_request(merged_at='2026-09-13T14:25:57+08:00')]))
        self.assertEqual(result[0]['merged_at'], '2026-09-13T06:25:57Z')

    def test_repository_url_and_pr_url_must_match(self):
        with self.assertRaisesRegex(ValueError, 'disagree'):
            updater.fetch_contributions(FakeAPI([pull_request(
                html_url='https://github.com/another/project/pull/1')]))

    def test_groups_counts_and_merge_order(self):
        result = updater.fetch_contributions(FakeAPI([
            pull_request('alice/project', 1, merged_at='2026-07-01T10:02:12Z'),
            pull_request('bob/project', 2),
            pull_request('alice/project', 3, merged_at='2026-08-01T10:02:12Z'),
        ]))
        note_url = 'https://github.com/bob/project/pull/2'
        rendered = updater.render_section(result, {note_url: 'A reviewed contribution.'})
        self.assertIn('3 merged PRs · 2 upstream projects', rendered)
        self.assertLess(rendered.index('[bob/project](https://github.com/bob/project)'),
                        rendered.index('[alice/project](https://github.com/alice/project)'))
        self.assertLess(rendered.index('[#3'), rendered.index('[#1'))
        self.assertIn('A reviewed contribution.', rendered)
        rows = [line for line in rendered.splitlines() if line.startswith('| ')][2:]
        self.assertEqual(len(rows), 3)
        self.assertIn('|  | [#1', rows[2])
        self.assertIn('| 2026-07-01 |', rows[2])
        self.assertNotIn('### ', rendered)

    def test_all_prs_visible_and_notes_collapsed(self):
        result = updater.fetch_contributions(FakeAPI([pull_request('alice/project', 1), pull_request('bob/project', 2)]))
        rendered = updater.render_section(result, {result[0]['url']: 'An optional detailed explanation.'})
        table, details = rendered.split('<details>', 1)
        for pr in result:
            self.assertIn(f"[#{pr['number']} — {pr['title']}]({pr['url']})", table)
        self.assertNotIn('An optional detailed explanation.', table)
        self.assertIn('<summary>Contribution notes</summary>', details)
        self.assertIn('An optional detailed explanation.', details)
        self.assertNotIn('<details open', rendered)
        self.assertEqual(rendered.count('<details>'), 1)
        self.assertEqual(rendered.count('</details>'), 1)

    def test_no_notes_does_not_add_empty_details(self):
        result = updater.fetch_contributions(FakeAPI([pull_request()]))
        for notes in [{}, {result[0]['url']: ''}, {result[0]['url']: '  \n  '}]:
            with self.subTest(notes=notes):
                rendered = updater.render_section(result, notes)
                self.assertIn('| Project | Merged PR | Merged (UTC) |', rendered)
                self.assertNotIn('<details', rendered)

    def test_empty_contributions_has_no_table(self):
        rendered = updater.render_section([], {})
        self.assertIn('0 merged PRs · 0 upstream projects', rendered)
        self.assertNotIn('| Project |', rendered)
        self.assertNotIn('<details', rendered)

    def test_invalid_note_is_rejected(self):
        result = updater.fetch_contributions(FakeAPI([pull_request()]))
        with self.assertRaisesRegex(ValueError, 'plain strings'):
            updater.render_section(result, {result[0]['url']: 7})

    def test_table_title_and_note_escaping(self):
        result = updater.fetch_contributions(FakeAPI([pull_request(title='Fix A | B <img> & C\nnext line')]))
        rendered = updater.render_section(result, {result[0]['url']: '*Note* | <script>\nnext line'})
        self.assertIn(r'Fix A \| B &lt;img&gt; &amp; C next line', rendered)
        self.assertIn(r'\*Note\* \| &lt;script&gt; next line', rendered)
        self.assertNotIn('<img>', rendered)
        self.assertNotIn('<script>', rendered)

    def test_titles_escape_markdown_and_html(self):
        value = '<img> [link](evil) *words* ' + chr(96) + 'code' + chr(96)
        escaped = updater.markdown_text(value)
        self.assertNotIn('<img>', escaped)
        self.assertIn('&lt;img&gt;', escaped)
        self.assertIn(r'\[link\]\(evil\)', escaped)
        self.assertIn(r'\*words\*', escaped)
        self.assertIn('\\' + chr(96), escaped)

    def test_only_marker_content_is_replaced(self):
        original = 'handwritten intro\n' + updater.START + '\nold\n' + updater.END + '\nhandwritten ending\n'
        updated = updater.replace_section(original, 'new')
        self.assertEqual(updated, 'handwritten intro\n' + updater.START + '\nnew\n' + updater.END + '\nhandwritten ending\n')

    def test_invalid_markers_fail(self):
        for value in ['missing', updater.START * 2 + updater.END, updater.END + updater.START]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                updater.replace_section(value, 'replacement')

    def profile_files(self, root):
        (root / 'data').mkdir()
        readme = 'intro\n' + updater.START + '\nold\n' + updater.END + '\nending\n'
        (root / 'README.md').write_text(readme, encoding='utf-8')
        (root / 'data/contribution-notes.json').write_text('{}', encoding='utf-8')
        (root / 'data/contributions.json').write_text('previous snapshot\n', encoding='utf-8')
        return readme

    def test_network_failure_preserves_both_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            readme = self.profile_files(root)
            def failed(_):
                raise RuntimeError('network failed')
            with self.assertRaises(RuntimeError):
                updater.update(root, failed)
            self.assertEqual((root / 'README.md').read_text(), readme)
            self.assertEqual((root / 'data/contributions.json').read_text(), 'previous snapshot\n')

    def test_incomplete_results_preserve_both_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            readme = self.profile_files(root)
            with self.assertRaises(ValueError):
                updater.update(root, lambda _: {'total_count': 1, 'incomplete_results': True, 'items': []})
            self.assertEqual((root / 'README.md').read_text(), readme)
            self.assertEqual((root / 'data/contributions.json').read_text(), 'previous snapshot\n')

    def test_dry_run_and_repeat_are_non_mutating(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            readme = self.profile_files(root)
            api = FakeAPI([pull_request()])
            self.assertTrue(updater.update(root, api, check=True))
            self.assertEqual((root / 'README.md').read_text(), readme)
            self.assertTrue(updater.update(root, api))
            mtimes = [(root / name).stat().st_mtime_ns for name in ['README.md', 'data/contributions.json']]
            self.assertFalse(updater.update(root, api))
            self.assertEqual(mtimes, [(root / name).stat().st_mtime_ns for name in ['README.md', 'data/contributions.json']])


if __name__ == '__main__':
    unittest.main()
