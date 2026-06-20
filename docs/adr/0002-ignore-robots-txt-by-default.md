# Ignore robots.txt by default; `--safe-mode` to honor it

The crawler **ignores `robots.txt` by default** and exposes `--safe-mode` to honor it.
Documentation is published to be read, we typically fetch a site only once (results are
cached for incremental re-crawls), and many docs frameworks ship overly broad robots rules
that would block legitimate doc fetching. To stay a good citizen despite ignoring robots,
an inter-request fetch delay always applies so large sites are not hammered, and an
identifiable User-Agent is sent.

## Consequences

This inverts the usual web-crawler norm, so a future reader would otherwise wonder why the
rule is skipped — hence this record. It is a per-run flag and trivially reversible, but the
*default* is a deliberate policy choice. `--safe-mode` exists for any site or context where
respecting `robots.txt` is required.
