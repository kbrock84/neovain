//! Step validation: what a step may do before Neovim runs it.

/// The command of an ex step, past its range: ":%s/a/b/" is "s", ":10,20w" is "w", ":q!" is "q".
pub(crate) fn ex_word(cmd: &str) -> &str {
    // Strip a leading range like "%", "'<,'>", "10,20", ".,+3" so ":%norm" is caught too.
    let body = cmd.trim_start_matches(|c: char| c.is_ascii_digit() || ",.;$%'<>+-/?^ ".contains(c));
    &body[..body.bytes().take_while(u8::is_ascii_alphabetic).count()]
}

/// Ex commands that write the file or quit Neovim. neovain writes the file itself, once every
/// step has succeeded, and needs Neovim alive until its report has been written: either of these
/// in a step would take the transactional guarantee out of neovain's hands.
pub(crate) const WRITE_OR_QUIT: &[&str] = &[
    "w", "write", "wa", "wal", "wall", "wq", "wqall", "wqa", "x", "xa", "xall", "xit", "exit", "q", "quit", "qa",
    "qall", "quall", "quitall", "clo", "close", "cq", "cquit", "sus", "suspend", "st", "stop",
];

/// Whether a step would write the file or quit Neovim. Such a step is always a mistake: the
/// tool already writes the file, and only after the whole sequence has succeeded.
pub(crate) fn write_or_quit_violation(step: &str) -> Option<&'static str> {
    const WHY: &str =
        "a step may not write the file or quit Neovim; neovain writes it itself, after every step has succeeded";
    match step.strip_prefix(':') {
        Some(cmd) => WRITE_OR_QUIT.contains(&ex_word(cmd)).then_some(WHY),
        None => keys_write_or_quit(step).then_some(WHY),
    }
}

/// The keys of a normal-mode step, one at a time, with `<...>` kept as a single key.
pub(crate) fn key_tokens(step: &str) -> Vec<&str> {
    let mut out = Vec::new();
    let mut i = 0;
    while i < step.len() {
        let end = match step.as_bytes()[i] {
            b'<' => step[i + 1..].find('>').map(|n| i + n + 2),
            _ => None,
        };
        let end = end.unwrap_or_else(|| i + step[i..].chars().next().map_or(1, char::len_utf8));
        out.push(&step[i..end]);
        i = end;
    }
    out
}

/// Whether `key` is the special key `name`, in any case: `<Esc>`, `<cr>`, `<Return>`.
pub(crate) fn is_key(key: &str, name: &str) -> bool {
    key.len() == name.len() + 2
        && key.starts_with('<')
        && key.ends_with('>')
        && key[1..key.len() - 1].eq_ignore_ascii_case(name)
}

/// Whether normal-mode keys would write the file or quit Neovim: `ZZ`/`ZQ`, or a `:w<CR>` typed
/// out. Keys typed in insert mode, in a search pattern or after `f`/`t`/`m` are text rather than
/// commands, so `ciwBUZZ<Esc>` renames a variable instead of being rejected.
pub(crate) fn keys_write_or_quit(step: &str) -> bool {
    #[derive(PartialEq)]
    enum Mode {
        Normal,
        Insert,
        Cmd,
        Search,
    }
    let keys = key_tokens(step);
    let (mut mode, mut typed, mut i) = (Mode::Normal, String::new(), 0usize);
    while i < keys.len() {
        let key = keys[i];
        let escape = matches!(mode, Mode::Insert | Mode::Cmd | Mode::Search)
            && (is_key(key, "Esc") || is_key(key, "C-[") || is_key(key, "C-c"));
        match mode {
            _ if escape => mode = Mode::Normal,
            Mode::Normal => match key {
                "i" | "I" | "a" | "A" | "o" | "O" | "c" | "C" | "s" | "S" | "R" => mode = Mode::Insert,
                ":" | "Q" => {
                    typed.clear();
                    mode = Mode::Cmd;
                }
                "/" | "?" => mode = Mode::Search,
                // The next key is a target, not a command: f:, t:, m: and friends.
                "f" | "F" | "t" | "T" | "r" | "m" | "'" | "`" | "\"" => i += 1,
                "Z" if i + 1 < keys.len() && matches!(keys[i + 1], "Z" | "Q") => return true,
                _ => {}
            },
            Mode::Insert => {
                if is_key(key, "Insert") {
                    mode = Mode::Normal;
                }
            }
            Mode::Cmd => {
                if is_key(key, "CR") || is_key(key, "Return") {
                    if WRITE_OR_QUIT.contains(&ex_word(&typed)) {
                        return true;
                    }
                    mode = Mode::Normal;
                } else {
                    typed.push_str(key);
                }
            }
            Mode::Search => {
                if is_key(key, "CR") || is_key(key, "Return") {
                    mode = Mode::Normal;
                }
            }
        }
        i += 1;
    }
    false
}

/// Ex commands that leave the buffer or run code outside it. In ex-only mode these are
/// rejected too: they are the obvious way out of the buffer the mode is meant to keep.
pub(crate) const EX_ONLY_NO_CODE: &[&str] = &[
    "lua", "luado", "luafile", "py", "pyfile", "python", "perl", "perlfile", "ruby", "rubyfile", "source", "so",
    "runtime", "ru", "earlier", "later", "term", "terminal",
];

/// In ex-only mode, reject steps that type normal-mode keys, directly or via :normal / :exe.
pub(crate) fn ex_only_violation(step: &str) -> Option<&'static str> {
    if step.starts_with('@') {
        return None;
    }
    let Some(cmd) = step.strip_prefix(':') else {
        return Some("normal-mode keys are disabled (NEOVAIN_EX_ONLY); use @anchor and :ex steps");
    };
    let body = cmd.trim_start_matches(|c: char| c.is_ascii_digit() || ",.;$%'<>+-/?^ ".contains(c));
    let word = ex_word(cmd);
    let is_normal = word.len() >= 4 && "normal".starts_with(word);
    let is_exe = word.len() >= 3 && "execute".starts_with(word);
    let in_global = (word.starts_with('g') || word.starts_with('v'))
        && ("global".starts_with(word) || "vglobal".starts_with(word))
        && body.contains("norm");
    if is_normal || is_exe || in_global {
        return Some(":normal/:execute are disabled (NEOVAIN_EX_ONLY)");
    }
    if body.starts_with('!') {
        return Some(":! runs a shell command; ex-only steps stay in the buffer (NEOVAIN_EX_ONLY)");
    }
    EX_ONLY_NO_CODE.contains(&word).then_some("commands that run code outside the buffer are disabled (NEOVAIN_EX_ONLY)")
}

#[cfg(test)]
mod tests {
use super::*;

    #[test]
    fn ex_only_rules() {
        for ok in ["@^def", ":%s/a/b/g", ":g/# DEBUG$/d", ":10,20m$", ":call append(3, ['x'])", ":n", ":nohl"] {
            assert!(ex_only_violation(ok).is_none(), "{ok}");
        }
        for bad in ["dd", "ciwx<Esc>", ":norm dd", ":normal! dd", ":%norm A;", ":'<,'>normal x", ":exe \"norm dd\"",
            ":g/x/norm dd", ":v/x/normal dd", ":!touch X", ":!!", ":lua print(1)", ":luado print(1)", ":luafile f.lua",
            ":py pass", ":py3 pass", ":pyfile f.py", ":perl 1", ":ruby 1", ":source f.vim", ":so f.vim", ":runtime f.vim",
            ":earlier", ":later", ":terminal"] {
            assert!(ex_only_violation(bad).is_some(), "{bad}");
        }
        // A style restriction, not a sandbox: ex commands can still run vimscript.
        for ok in [":call system('echo hi')", ":read f.txt", ":wincmd h"] {
            assert!(ex_only_violation(ok).is_none(), "{ok}");
        }
    }
    #[test]
    fn steps_that_write_or_quit_are_rejected() {
        for ok in ["@^def", ":%s/a/b/g", ":10,20m$", ":g/# DEBUG$/d", ":d", ":sort", ":set ff=unix", ":nohl",
            ":call append(3, ['x'])", "dd", "ciwnewname<Esc>", "iZZ<Esc>", "/zzz<CR>", "f:dw", "mZ", ">"] {
            assert!(write_or_quit_violation(ok).is_none(), "{ok}");
        }
        for bad in [":w", ":w!", ":write", ":1,5w", ":%w", ":wq", ":x", ":xit", ":q", ":q!", ":qa", ":wall",
            ":quitall", ":cq", "ZZ", "ZQ", "ddZZ", "GZZ", ":w<CR>", "Qw<CR>"] {
            assert!(write_or_quit_violation(bad).is_some(), "{bad}");
        }
    }
    #[test]
    fn keys_that_only_look_like_a_write_are_left_alone() {
        // "ZZ" typed in insert mode is text; "ZZ" after <Esc> saves and quits.
        assert!(!keys_write_or_quit("ciwBUZZ<Esc>"));
        assert!(!keys_write_or_quit("iZZ<Esc>"));
        assert!(!keys_write_or_quit(":%s/ZZ//"));
        assert!(!keys_write_or_quit("/a:ZZ<CR>"));
        assert!(keys_write_or_quit("iZZ<Esc>ZZ"));
        assert!(keys_write_or_quit("dd<Esc>:w<CR>"));
    }
}
