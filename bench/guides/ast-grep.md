# Editing files with ast-grep (for agents)

ast-grep finds code by its syntax tree and rewrites every match, in every file you point it
at:

```
ast-grep run -l python -p 'PATTERN' -r 'REWRITE' -U PATH...
```

- `-p` is a pattern written as code in the language. `$NAME` matches one node (an expression,
  a name, an argument); `$$$NAME` matches a sequence (all the arguments, all the names in an
  import). The pattern must parse as code on its own: `foo($A, $B)` works, `foo($A,` does
  not. A bare name, `-p 'old_name'`, matches that identifier wherever it is a whole
  identifier: a definition, an import, a call, the attribute in `x.old_name`. It does not
  match `old_name_extra`, nor text inside strings or comments.
- `-r` is the replacement, with the same metavariables: `-r 'bar($A, key=$B)'`.
- **Without `-U` nothing is written**: ast-grep prints the changes it would make, as a diff.
  That is the preview. `-U` (`--update-all`) applies them.
- Pass `-l python` (or the language) every time. A PATH can be a directory: every file of
  that language under it is rewritten in the one call.
- Exit code 1 means nothing matched. A pattern that does not parse prints a warning and
  matches nothing.

## Rules, for what a pattern cannot say

A node picked by kind, a node with a certain child, or a match only inside something else:

```
ast-grep scan -U --inline-rules 'id: NAME
language: python
rule:
  kind: keyword_argument
  has: {field: name, regex: ^verbose$}
fix: ""' PATH...
```

- `kind` is the tree-sitter node kind: `call`, `identifier`, `keyword_argument`,
  `expression_statement`, `function_definition`, `import_from_statement`, ...
  `ast-grep run -l python -p 'CODE' --debug-query=ast PATH` prints the kinds in CODE.
- `pattern`, `kind` and `regex` match one node; `has`, `inside`, `follows` and `precedes`
  relate it to others (`stopBy: end` searches all the way up or down); `all`, `any` and `not`
  combine rules.
- `fix` replaces the matched node. Removing a node leaves the text around it: a removed
  keyword argument leaves its comma, a removed statement leaves an empty line.
  `fix: {template: "", expandStart: {regex: ', ?'}}` widens the removal to the comma and
  space before the node.
- Several rules in one call are separated by a line with `---`.

## Using it well

1. **Read first** (`rg -n`, `cat -n`) so you know every form the code takes: calls that span
   several lines, calls through an attribute (`mod.func(...)`), names that only contain the
   one you want.
2. **Preview, then apply.** Run without `-U`, read the diff, then run the same command with
   `-U`. One call can cover every file; you do not need a call per file.
3. **The template sets the layout.** A match that spanned several lines comes back on the
   template's lines, and a removed node leaves its line behind. Check the result (`cat -n`,
   or a diff against a copy) and tidy what the task requires.
4. **It only rewrites what it matched.** ast-grep cannot move code from one place to another:
   to move a block, the replacement has to contain the block's text.
5. **Quote patterns in single quotes** so the shell leaves `$A` alone.
