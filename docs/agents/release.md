# Releasing to the public `master` branch

`master` is the public default branch and a strict **subset** of `development`. Never
`git merge development` into master — that drags the AI-dev scaffolding (`.sandcastle/`,
`package.json`, `AGENTS.md`, `docs/agents/`, `CLAUDE.md`, in-repo skills) onto the public
branch. Publish by copying a fixed whitelist instead.

## How to publish

Run the in-repo skill from a clean `development` branch:

```
/publish-to-master
```

It rebuilds `master` from the whitelist in
[`.claude/skills/publish-to-master/SKILL.md`](../../.claude/skills/publish-to-master/SKILL.md),
verifies no scaffolding leaked, commits, and switches you back to `development`. Pushing
`master` is gated — review the diff, then `git push origin master` yourself.

See [`adr/0001-two-branch-whitelist-release.md`](adr/0001-two-branch-whitelist-release.md)
for the rationale and rejected alternatives.

## Adding a new public file

If you add a file that *users* should receive, add its path to the `WHITELIST` in the
publish skill. If you don't, it stays on development forever and never ships.

## Updating CLAUDE.md

`CLAUDE.md` is development-only and is never published. Update it (on development) whenever
a change alters how agents or contributors should work in this repo — it is the first file
an agent reads.
