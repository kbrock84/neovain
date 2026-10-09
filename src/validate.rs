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


// ---- the agent-safe profile ----

/// The ex commands the safe profile allows: buffer edits that evaluate nothing, execute
/// nothing and touch no file but the one being edited. An allow-list, so a command word the
/// parser does not recognize is rejected instead of passed through. Every entry was checked
/// against a real nvim (`:help`-documented name or probed short form).
pub(crate) const SAFE_COMMANDS: &[&str] = &[
    "a", "append", "c", "change", "co", "copy", "d", "delete", "g", "global", "i", "insert", "j", "join", "m",
    "move", "pu", "put", "retab", "s", "sort", "substitute", "t", "u", "undo", "v", "vglobal", "y", "yank",
];

/// Command modifiers the safe profile honors: they change messages, marks, the alternate file
/// and autocmds, never what the command does.
const SAFE_MODIFIERS: &[&str] = &["silent", "keepalt", "keepjumps", "keeppatterns", "lockmarks", "noautocmd"];

/// The text between two unescaped `delim` and what follows the closing one, or None when the
/// delimiter never closes.
fn segment(s: &str, delim: u8) -> Option<(&str, &str)> {
    let b = s.as_bytes();
    let mut i = 0;
    while i < b.len() {
        if b[i] == b'\\' {
            i += 2;
            continue;
        }
        if b[i] == delim {
            return Some((&s[..i], &s[i + 1..]));
        }
        i += 1;
    }
    None
}

/// Drop leading command modifiers (`silent! `), stopping at the first word that is not one.
fn strip_modifiers(mut s: &str) -> &str {
    loop {
        let before = s;
        for m in SAFE_MODIFIERS {
            if let Some(rest) = s.strip_prefix(m) {
                let rest = rest.strip_prefix('!').unwrap_or(rest);
                if rest.is_empty() || rest.starts_with(char::is_whitespace) {
                    s = rest.trim_start();
                    break;
                }
            }
        }
        if s == before {
            return s;
        }
    }
}

/// One address: `%`, `.`, `$`, digits, `'x`, `` `x ``, `+1`, `-1`, `/pat/`, `?pat?`. An
/// unterminated pattern consumes nothing: the caller then reads no command word and fails
/// closed.
fn one_address(s: &str) -> &str {
    let b = s.as_bytes();
    if b.is_empty() {
        return s;
    }
    match b[0] {
        b'%' | b'.' | b'$' => &s[1..],
        b'0'..=b'9' | b'+' | b'-' => &s[1 + b[1..].iter().take_while(|c| c.is_ascii_digit()).count()..],
        b'\'' | b'`' if b.len() >= 2 && b[1].is_ascii_alphanumeric() => &s[2..],
        b'/' | b'?' => {
            let delim = b[0];
            let mut i = 1;
            while i < b.len() {
                if b[i] == b'\\' {
                    i += 2;
                    continue;
                }
                if b[i] == delim {
                    return &s[i + 1..];
                }
                i += 1;
            }
            s
        }
        _ => s,
    }
}

/// The address range: one or more addresses separated by `,` or `;`.
fn strip_range(s: &str) -> (&str, bool) {
    let (mut r, mut n) = (s, 0);
    loop {
        let before = r;
        r = one_address(r);
        if r == before {
            break;
        }
        n += 1;
        match r.as_bytes().first() {
            Some(b',') | Some(b';') => r = &r[1..],
            _ => break,
        }
    }
    (r, n > 0)
}

/// The command word of an ex step (already without its `:`) and what follows it, past
/// modifiers and the address range. None when there is no alphabetic command word at all,
/// which the safe profile treats as a violation: nothing it cannot read may run.
fn ex_head(cmd: &str) -> Option<(&str, &str)> {
    let mut s = cmd;
    loop {
        let (after_range, _) = strip_range(strip_modifiers(s));
        if after_range == s {
            break;
        }
        s = after_range;
    }
    let word_end = s.bytes().take_while(|b| b.is_ascii_alphabetic()).count();
    let (word, tail) = s.split_at(word_end);
    (!word.is_empty()).then_some((word, tail))
}

const NOT_ALLOWED: &str =
    "not an allow-listed buffer edit; the safe profile runs no code, no shell, no file I/O and no chaining";

/// Whether a full ex step (without its `:`) passes the safe profile, and why not.
fn safe_ex(cmd: &str) -> Option<&'static str> {
    let Some((word, tail)) = ex_head(cmd) else {
        return Some("cannot read the command word of this step (safe profile)");
    };
    // Uppercase words are rejected with the rest: nvim itself only knows lowercase commands.
    if !word.bytes().all(|b| b.is_ascii_lowercase()) || !SAFE_COMMANDS.contains(&word) {
        return Some(NOT_ALLOWED);
    }
    match word {
        // Move, copy and friends take an address and nothing else: junk behind them is a
        // violation, and an address that eats a `|` is a chain.
        "m" | "move" | "t" | "co" | "copy" => address_tail(tail),
        "s" | "substitute" => substitute_tail(tail),
        "g" | "v" | "global" | "vglobal" => global_tail(tail),
        "pu" | "put" => put_tail(tail),
        _ => plain_tail(tail),
    }
}

/// The argument of a command that only takes an address: an address, then nothing. nvim
/// rejects the incomplete forms with E16, and so does the profile, before it starts.
fn address_tail(tail: &str) -> Option<&'static str> {
    let (rest, had_range) = strip_range(tail.trim_start());
    if !had_range || !rest.trim().is_empty() {
        return Some("this command needs an address and nothing else (safe profile)");
    }
    None
}

/// `:put` with an optional `!` and then a register: `=` evaluates vimscript, so it is
/// refused in every form it can be written in -- `:put =x`, `:put! =x`, `:put!=x`, under a
/// modifier or nested in `:g`. A normal register is a buffer edit like any other.
fn put_tail(tail: &str) -> Option<&'static str> {
    let mut rest = tail.trim_start();
    while let Some(after) = rest.strip_prefix('!') {
        rest = after.trim_start();
    }
    if rest.starts_with('=') {
        return Some("the = expression register evaluates vimscript (safe profile)");
    }
    plain_tail(tail)
}

/// Chaining is off everywhere: `|` would run a second command this profile never checked.
fn plain_tail(tail: &str) -> Option<&'static str> {
    tail.contains('|').then_some("`|` chains a second command (disabled in the safe profile)")
}

/// `:s/{pattern}/{replacement}/{flags}`, with no expression replacement.
fn substitute_tail(tail: &str) -> Option<&'static str> {
    if tail.is_empty() {
        return None;
    }
    let delim = tail.as_bytes()[0];
    if delim.is_ascii_alphanumeric() || delim == b'\\' || delim.is_ascii_whitespace() {
        return Some("cannot read the delimiters of this :s (safe profile)");
    }
    let Some((_, after_pat)) = segment(&tail[1..], delim) else {
        return Some("unterminated pattern in this :s (safe profile)");
    };
    let Some((repl, flags)) = segment(after_pat, delim) else {
        return Some("unterminated replacement in this :s (safe profile)");
    };
    if repl.contains("\\=") {
        return Some("a \\= replacement evaluates vimscript (safe profile)");
    }
    if !flags.bytes().all(|b| b.is_ascii_alphabetic()) {
        return Some("trailing text after the :s flags (safe profile)");
    }
    None
}

/// `:g/{pattern}/{command}`, where the command goes through the whole profile again.
fn global_tail(tail: &str) -> Option<&'static str> {
    let b = tail.as_bytes();
    if b.is_empty() {
        return Some(":g needs a pattern (safe profile)");
    }
    let delim = b[0];
    if delim.is_ascii_alphanumeric() || delim == b'\\' || delim.is_ascii_whitespace() {
        return Some("cannot read the pattern of this :g (safe profile)");
    }
    let Some((_, rest)) = segment(&tail[1..], delim) else {
        return Some("unterminated :g pattern (safe profile)");
    };
    // An empty :g command only prints, and the rest is validated as if it stood alone.
    if rest.is_empty() {
        None
    } else {
        safe_ex(rest)
    }
}

/// A step the agent-safe profile accepts, or why not. Anchors are pure cursor movement;
/// ex steps must parse and sit on the allow-list; anything else, keys included, is rejected.
pub(crate) fn safe_violation(step: &str) -> Option<&'static str> {
    if step.starts_with('@') {
        return None;
    }
    match step.strip_prefix(':') {
        None => Some("normal-mode keys are disabled in the safe profile; use @anchor and :ex steps"),
        Some(cmd) => safe_ex(cmd),
    }
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

    #[test]
    fn the_safe_profile_allows_buffer_edits() {
        for ok in ["@^def", "@2@open(", ":%s/a/b/g", ":1s/a/b/", ":.,$d", ":2m$", ":10,20t$", ":2co$",
            ":g/^#/d", ":v/^import/d", ":g/^x/s/a/b/", ":g/a\\/b/d", ":put a", ":put! a", ":2y", ":sort u", ":1retab",
            ":silent s/a/b/", ":silent! 1d", ":keepjumps 1,2d", ":/$t/d", ":?a?d", ":'a,'bd", ":1,2join",
            ":u", ":undo", ":c", ":append", ":sort!", ":change", ":g/^t/d", ":s", ":1,+2m$"] {
            assert!(safe_violation(ok).is_none(), "{ok}");
        }
    }

    #[test]
    fn the_safe_profile_rejects_code_shell_files_and_chaining() {
        for bad in [
            // keys, and the ex commands that run code or leave the buffer
            "dd", "ciwx<Esc>", "ZZ", ":call system('id')", ":echo system('id')", ":let @a=1", ":if 1",
            ":execute '!ls'", ":execute 'norm dd'", ":normal ZZ", ":norm dd", ":lua os.exit(0)",
            ":luafile f.lua", ":py pass", ":py3 pass", ":perl 1", ":ruby 1", ":source f.vim", ":so f.vim",
            ":runtime f.vim", ":terminal", ":!", ":!touch X", ":term", ":finish", ":function! F()",
            // files: write, quit, read, edit, move in time
            ":w", ":wq", ":q", ":w other.txt", ":x", ":read /etc/passwd", ":r !ls", ":e other.txt",
            ":saveas /tmp/x", ":earlier 3f", ":later",
            // expressions and chaining, including nested in :g and through modifiers
            ":s/a/\\=system('id')/", ":s/a/\\=submatch(0)/e", ":put =system('id')", ":put! =1", ":put!=system('id')",
            ":silent put! =system('id')", ":silent! put =1", ":g/^x/put! =system('id')", ":v/^x/put =1",
            ":pu!! =1", ":s/a/b/ | !ls",
            ":2d | call system('id')", ":g/^x/!touch Y", ":g/^x/normal ZZ", ":g/^x/call system('id')",
            ":g/^x/s/a/\\=system(1)/", ":g/^x/ | !ls", ":silent lua os.exit(0)", ":silent! call system('1')",
            ":v/^x/!touch Z", ":1,2d|d",
            // things the parser must not mistake for an allow-listed word
            ":se nu", ":debug 1", ":de", ":co mmand", ":S/a/b/", ":D", ":Q", ":g", ":v",
            ":silent", ":1,2", ":", ":/unterminated", ":'a", ":2m", ":s/foo", ":s/foo/bar", "/n"
        ] {
            assert!(safe_violation(bad).is_some(), "{bad}");
        }
    }

    #[test]
    fn the_safe_profile_reads_ranges_that_look_like_commands() {
        // The command word is past the range, whatever the range looks like: a write hiding
        // behind a pattern or marks is not an allow-listed edit either.
        for bad in [":/needle/w! /tmp/x", ":'a,'bw", ":/a/b/c", ":1,2w"] {
            assert!(safe_violation(bad).is_some(), "{bad}");
        }
        for ok in [":/needle/d", ":'a,'bt$"] {
            assert!(safe_violation(ok).is_none(), "{ok}");
        }
    }
}
