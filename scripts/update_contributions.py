#!/usr/bin/env python3
"""Refresh the profile's verified public, external, merged pull requests."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import html
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

USERNAME = 'VeraPyuyi'
API = 'https://api.github.com'
START = '<!-- CONTRIBUTIONS:START -->'
END = '<!-- CONTRIBUTIONS:END -->'
ROOT = Path(__file__).resolve().parents[1]
PROJECT_NAMES = {
    'LodyAI/Lody': 'Lody',
    'heymrun/heym': 'heym',
    'langgenius/dify': 'Dify',
    'wanshuiyin/Auto-claude-code-research-in-sleep': 'ARIS',
}
REPOSITORY_URL = re.compile(r'https://api\.github\.com/repos/([^/]+/[^/]+)\Z')
PR_URL = re.compile(r'https://github\.com/([^/]+/[^/]+)/pull/([1-9][0-9]*)\Z')


def request_json(url: str) -> dict:
    headers = {
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': 'Pyuyi-profile-contributions',
    }
    token = os.environ.get('GITHUB_TOKEN')
    if token:
        headers['Authorization'] = f'Bearer {token}'
    for attempt in range(3):
        try:
            with urlopen(Request(url, headers=headers), timeout=30) as response:
                result = json.loads(response.read().decode('utf-8', errors='strict'))
            if not isinstance(result, dict):
                raise ValueError('GitHub returned an unexpected response.')
            return result
        except HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise RuntimeError(f'GitHub API request failed (HTTP {error.code}).') from error
        except (URLError, TimeoutError) as error:
            if attempt == 2:
                raise RuntimeError('GitHub API request failed after three attempts.') from error
        time.sleep(2 ** attempt)
    raise RuntimeError('GitHub API request failed.')


def merged_time(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError('A verified merge timestamp is required.')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Merge timestamp must include a timezone.')
    return result.astimezone(timezone.utc)


def search_items(fetch=request_json) -> list:
    items = {}
    total = None
    page = 1
    while True:
        query = urlencode({
            'q': f'is:pr is:merged is:public author:{USERNAME}',
            'per_page': 100,
            'page': page,
            'sort': 'created',
            'order': 'asc',
        })
        response = fetch(f'{API}/search/issues?{query}')
        count = response.get('total_count')
        batch = response.get('items')
        if response.get('incomplete_results') is not False:
            raise ValueError('GitHub search returned incomplete results; preserving the previous version.')
        if not isinstance(count, int) or isinstance(count, bool) or count < 0 or not isinstance(batch, list):
            raise ValueError('GitHub search returned invalid pagination data.')
        if count > 1000:
            raise ValueError('GitHub search exceeds its 1,000-result limit; preserving the previous version.')
        if total is None:
            total = count
        elif count != total:
            raise ValueError('Search results changed during pagination; please retry.')
        for item in batch:
            url = item.get('html_url')
            if not isinstance(url, str) or not PR_URL.fullmatch(url):
                raise ValueError('Search returned an invalid pull request URL.')
            items[url] = item
        if len(items) == total:
            return list(items.values())
        if len(items) > total or len(batch) < 100:
            raise ValueError('Search pagination is incomplete; preserving the previous version.')
        page += 1
        if page > 10:
            raise ValueError('Could not obtain a complete, unique set of search results.')


def fetch_contributions(fetch=request_json) -> list:
    contributions = {}
    for item in search_items(fetch):
        repository_match = REPOSITORY_URL.fullmatch(item.get('repository_url', ''))
        if not repository_match:
            raise ValueError('Search returned an invalid repository URL.')
        repository = repository_match.group(1)
        if repository.split('/')[0].casefold() == USERNAME.casefold():
            continue
        number = item.get('number')
        if not isinstance(number, int) or isinstance(number, bool) or number < 1:
            raise ValueError('Search returned an invalid pull request number.')
        pr = fetch(f'{API}/repos/{repository}/pulls/{number}')
        base_repository = pr.get('base', {}).get('repo', {})
        if pr.get('user', {}).get('login', '').casefold() != USERNAME.casefold():
            continue
        if base_repository.get('private') is True:
            continue
        if base_repository.get('private') is not False:
            raise ValueError('Pull request visibility could not be verified.')
        canonical_repo = base_repository.get('full_name', '')
        if '/' not in canonical_repo:
            raise ValueError('Pull request repository metadata is missing.')
        if canonical_repo.split('/')[0].casefold() == USERNAME.casefold():
            continue
        if pr.get('merged') is not True or not pr.get('merged_at'):
            raise ValueError('A search result is not verified as merged; preserving the previous version.')
        timestamp = merged_time(pr['merged_at'])
        url = pr.get('html_url', '')
        match = PR_URL.fullmatch(url)
        if not match or match.group(1).casefold() != canonical_repo.casefold() or int(match.group(2)) != number:
            raise ValueError('Pull request URL and repository metadata disagree.')
        title = pr.get('title')
        if not isinstance(title, str) or not title.strip():
            raise ValueError('Pull request title is missing.')
        contributions[url] = {
            'repository': canonical_repo,
            'number': number,
            'title': title,
            'url': url,
            'merged_at': timestamp.isoformat().replace('+00:00', 'Z'),
        }
    return sorted(contributions.values(), key=lambda pr: (pr['merged_at'], pr['url']), reverse=True)


def markdown_text(value: str) -> str:
    escaped = html.escape(' '.join(value.split()), quote=False)
    return re.sub(r'([\\\x60*_{}\[\]()|])', r'\\\1', escaped)


def render_section(contributions: list, notes: dict) -> str:
    groups = defaultdict(list)
    for pr in contributions:
        groups[pr['repository']].append(pr)
    repositories = sorted(groups, key=lambda repo: (max(pr['merged_at'] for pr in groups[repo]), repo), reverse=True)
    lines = [f'**✦ {len(contributions)} merged PRs · {len(groups)} upstream projects ✦**']
    if not contributions:
        lines.extend(['', 'A small collection of contributions, waiting for its first star.'])
        return '\n'.join(lines)
    lines.extend(['', '| Project | Merged PR | Merged (UTC) |', '| :--- | :--- | :--- |'])
    descriptions = []
    for repo in repositories:
        project_name = markdown_text(PROJECT_NAMES.get(repo, repo))
        project_link = f'[{project_name}](https://github.com/{repo})'
        for index, pr in enumerate(sorted(groups[repo], key=lambda value: (value['merged_at'], value['number']), reverse=True)):
            project = project_link if index == 0 else ''
            date = merged_time(pr['merged_at']).strftime('%Y-%m-%d')
            lines.append(f"| {project} | [#{pr['number']} — {markdown_text(pr['title'])}]({pr['url']}) | {date} |")
            note = notes.get(pr['url'])
            if note is not None and not isinstance(note, str):
                raise ValueError('Contribution notes must be plain strings.')
            if note and note.strip():
                descriptions.append(f"- [{project_name} #{pr['number']}]({pr['url']}) — {markdown_text(note)}")
    if descriptions:
        lines.extend(['', '<details>', '<summary>Contribution notes</summary>', '', *descriptions, '', '</details>'])
    return '\n'.join(lines).rstrip()


def replace_section(readme: str, section: str) -> str:
    if readme.count(START) != 1 or readme.count(END) != 1:
        raise ValueError('README must contain exactly one pair of contribution markers.')
    start = readme.index(START) + len(START)
    end = readme.index(END)
    if end < start:
        raise ValueError('Contribution markers are reversed.')
    return readme[:start] + '\n' + section + '\n' + readme[end:]


def write_if_changed(path: Path, content: str) -> bool:
    if path.exists() and path.read_text(encoding='utf-8') == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        output.write(content)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def update(root: Path = ROOT, fetch=request_json, check: bool = False) -> bool:
    readme_path = root / 'README.md'
    readme = readme_path.read_text(encoding='utf-8', errors='strict')
    notes = json.loads((root / 'data/contribution-notes.json').read_text(encoding='utf-8', errors='strict'))
    if not isinstance(notes, dict):
        raise ValueError('Contribution notes must be a URL-to-description mapping.')
    contributions = fetch_contributions(fetch)
    updated = replace_section(readme, render_section(contributions, notes))
    if '\ufffd' in updated:
        raise ValueError('Refusing to publish text containing replacement characters.')
    snapshot = json.dumps(contributions, ensure_ascii=False, indent=2) + '\n'
    snapshot_path = root / 'data/contributions.json'
    changed = updated != readme or not snapshot_path.exists() or snapshot_path.read_text(encoding='utf-8') != snapshot
    if not check:
        write_if_changed(snapshot_path, snapshot)
        write_if_changed(readme_path, updated)
    repositories = len({pr['repository'] for pr in contributions})
    print(f'Verified {len(contributions)} merged external PRs across {repositories} projects; '
          f'{"changes detected" if changed else "already up to date"}.')
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Check for changes without writing files.')
    args = parser.parse_args()
    try:
        changed = update(check=args.check)
    except (OSError, ValueError, RuntimeError) as error:
        print(f'Contribution refresh failed: {error}', file=sys.stderr)
        return 1
    return int(args.check and changed)


if __name__ == '__main__':
    raise SystemExit(main())
