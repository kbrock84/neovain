# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning]
(<https://semver.org/spec/v2.0.0.html>).

## [Unreleased]

### Fixed

- **A step may no longer write the file or end Neovim.** `:w`, `:wq`, `:x`,
  `:q`, `ZZ`, `ZQ` and their relatives are rejected as usage errors (exit 2).
  They broke the transactional guarantee: `:w` wrote what came before it and
  the run still reported success, and `ZZ` wrote the buffer even under
  `--dry-run`. Neovim now edits a copy of the file, and neovain writes the
  real one once every step has succeeded (`ac8f206`, `d67ffa8`).
- **A step that quits Neovim is a failed step.** It leaves without a report,
  which used to surface as a setup error. The buffer was a copy, so the file
  really is unchanged: the run now fails with exit 1 and "file unchanged"
  (`ac8f206`).
- **ex-only mode also rejects the ex commands that leave the buffer:** `:!`,
  `:lua`/ `:luado`/`:luafile`, `:py*`, `:perl`, `:ruby`, `:source`,
  `:runtime`, `:earlier`/`:later`. The contract stays what it says it is, a
  style restriction and not a sandbox: ex commands are vimscript and `:call
  system('...')` still runs a shell (`7879b53`).

### Added

- **A warning for the line a short range leaves behind.** When a range stops
  short while moving or deleting a block, its last lines end up beside a line
  that cannot hold them, and nothing said so. The summary now warns `line N is
  indented but no enclosing block starts above it (left behind by a range?)`,
  but only where the edit created the situation, so a file that already stood
  that way is not warned about on every change to it (`a8d9bf5`).

### Documentation

- The README and [neovain.dev](https://neovain.dev) document the single-writer
  guarantee, the full ex-only contract and the new warning.

[Unreleased]: https://github.com/alfonsodg/neovain/compare/v0.2.0...HEAD
