# Profile maintenance

The profile is published from `VeraPyuyi/VeraPyuyi`, with English copy and original anime artwork.

## Merged contributions

The workflow runs daily at 10:17 Asia/Shanghai and can also be run from **Actions → Refresh merged contributions → Run workflow**. GitHub schedules can run late, and GitHub may disable scheduled workflows after a long period of repository inactivity; a manual run remains available.

Run the updater locally with `python3 scripts/update_contributions.py`. `--check` verifies the current data without writing. An optional `GITHUB_TOKEN` environment variable increases the API allowance; the workflow uses GitHub's built-in token.

Only public, merged PRs authored by `VeraPyuyi` in repositories owned by others are counted. Search results are paginated and each external PR is checked against its actual PR metadata. Dates are displayed in UTC. Failed requests, incomplete search results, invalid metadata, and marker errors fail the refresh. The previous README stays in place until all network checks and rendering succeed.

`data/contributions.json` is the generated snapshot. All PRs appear in a compact three-column table, grouped by project and ordered by the actual merge date. Edit `data/contribution-notes.json` to add or revise a short description, keyed by the canonical PR URL. Descriptions are kept in the collapsed **Contribution notes** section; the section is omitted when no descriptions exist. Newly merged PRs appear with their complete official title, link, and date even when no description has been written yet.

Only the text between `CONTRIBUTIONS:START` and `CONTRIBUTIONS:END` is generated. Keep exactly one pair of these markers. Introduction, links, artwork, personal project, and closing quote stay outside the generated block. A refresh that changes nothing produces no commit. Concurrent pushes fail safely instead of overwriting another edit.

Run tests with `python3 -m unittest discover -s tests -v`. The updater and tests use only the Python standard library.

## Design references

- [Anurag Hazra](https://github.com/anuraghazra/anuraghazra): compact introduction and focused links.
- [DenverCoder1](https://github.com/DenverCoder1/DenverCoder1): clear separation of contributions and personal projects.
- [HerXayah](https://github.com/HerXayah/HerXayah): gentle anime-themed accents.

The layout is independently written; the banner depicts Pyuyi's original researcher and star cat.
