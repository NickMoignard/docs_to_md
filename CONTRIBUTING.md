# Contributing to docs-to-okf

Thanks for your interest in improving docs-to-okf! This project uses the standard
**fork-and-pull-request** model. You don't need write access to contribute.

## Before you start

- **Found a bug or have an idea?** [Open an issue](https://github.com/NickMoignard/docs_to_okf/issues)
  first. For anything beyond a small fix, it's worth agreeing on the approach before you
  write code.
- **Read [`CONTEXT.md`](CONTEXT.md).** It's the project's glossary. We're deliberate about
  vocabulary — e.g. **Tool** (capital T) always means *the external product whose docs are
  being mirrored*, never the crawler or the skill. Matching this language keeps the codebase
  and the discussion coherent.

## The fork-and-PR flow

1. **Fork** the repository to your own account (the *Fork* button on GitHub).

2. **Clone your fork** and create a branch off `master`:

   ```bash
   git clone https://github.com/<your-username>/docs_to_okf.git
   cd docs_to_okf
   git checkout -b my-change
   ```

3. **Make your change.** The skill is three files — `SKILL.md` (orchestration),
   `crawl.py` (the crawler), and `scripts/validate.sh` (the bundled OKF validator). See
   [`README.md`](README.md) for how they fit together and
   [`docs/adr/`](docs/adr/) for the reasoning behind key design decisions.

4. **Run the tests** and make sure they pass:

   ```bash
   uv run --extra dev pytest
   ```

   Add tests for any behaviour you change or add.

5. **Commit and push** to your fork:

   ```bash
   git commit -m "Describe your change"
   git push origin my-change
   ```

6. **Open a pull request** against `master` on this repository. Describe what you changed
   and why, and link any related issue.

## What happens next

- A maintainer reviews your PR. **Merging requires maintainer approval** — PRs are not
  self-mergeable, so nothing lands on `master` without review.
- Keep PRs focused: one logical change per PR is much easier to review and merge.
- Be patient and constructive in review discussion. 🙂

## License

By contributing, you agree that your contributions are licensed under the
[MIT License](LICENSE) that covers this project.
