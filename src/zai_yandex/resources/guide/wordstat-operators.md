# Wordstat queries and operators

Docs: https://yandex.ru/support2/wordstat/ru/content/operators
- `"phrase"` - only this phrase without extra words (word forms allowed).
- `!word` - fix the word form; `+word` - force a stop word (предлоги).
- `-word` - exclude a word; `[word1 word2]` - fix word order; `(a|b)` - alternatives.
- Tools: `yandex_wordstat_get_top` (phrase + related, monthly counts), `yandex_wordstat_dynamics` (by month/week/day), `yandex_wordstat_regions` (share and affinity index by region), `yandex_wordstat_regions_tree` (region ids).
- Each call is paid/limited: collect seeds first, then query; reuse results; never loop over hundreds of phrases without the user's go-ahead.
- Clustering: group phrases by intent (commercial / informational / navigational) and by product; drop phrases with negligible counts.
