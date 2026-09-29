//! End-to-end tests: run the built binary against real files. Requires `nvim` on PATH
//! (or NEOVAIN_NVIM); tests are skipped with a message if it is missing.

use std::fs;
use std::path::PathBuf;
use std::process::{Command, Output};

const SAMPLE: &str = "def load(path):\n    data = open(path).read()\n    return data\n\ndef save(path, data):\n    open(path, \"w\").write(data)\n\ndef main():\n    d = load(\"in.txt\")\n    save(\"out.txt\", d)\n";

fn have_nvim() -> bool {
    let nvim = std::env::var_os("NEOVAIN_NVIM").unwrap_or_else(|| "nvim".into());
    let ok = Command::new(nvim).arg("--version").output().is_ok();
    if !ok {
        assert!(std::env::var_os("NEOVAIN_REQUIRE_NVIM").is_none(), "nvim required but not found");
        eprintln!("skipping: nvim not found");
    }
    ok
}

struct Case {
    _dir: tempfile::TempDir,
    path: PathBuf,
}

impl Case {
    fn new(content: &[u8]) -> Self {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("f.py");
        fs::write(&path, content).unwrap();
        Case { _dir: dir, path }
    }
    fn run(&self, args: &[&str]) -> Output {
        Command::new(env!("CARGO_BIN_EXE_neovain"))
            .arg(&self.path)
            .args(args)
            .env_remove("MSYSTEM")
            .output()
            .unwrap()
    }
    fn text(&self) -> String {
        fs::read_to_string(&self.path).unwrap()
    }
}

fn stderr(o: &Output) -> String {
    String::from_utf8_lossy(&o.stderr).into_owned()
}

#[test]
fn anchor_then_keys_edits_and_prints_diff() {
    if !have_nvim() {
        return;
    }
    let c = Case::new(SAMPLE.as_bytes());
    let o = c.run(&["@^def load", "wciwread_file<Esc>"]);
    assert!(o.status.success(), "{}", stderr(&o));
    let out = String::from_utf8_lossy(&o.stdout);
    assert!(out.contains("-def load(path):") && out.contains("+def read_file(path):"), "{out}");
    assert!(c.text().starts_with("def read_file(path):\n"));
}

#[test]
fn ex_substitute() {
    if !have_nvim() {
        return;
    }
    let c = Case::new(SAMPLE.as_bytes());
    assert!(c.run(&[r":%s/\<load(/read_file(/g"]).status.success());
    assert_eq!(c.text().matches("read_file(").count(), 2);
}

#[test]
fn ambiguous_anchor_fails_and_leaves_file_untouched() {
    if !have_nvim() {
        return;
    }
    let c = Case::new(SAMPLE.as_bytes());
    let o = c.run(&["@open(", "dd"]);
    assert_eq!(o.status.code(), Some(1));
    assert!(stderr(&o).contains("anchor matched 2 lines (2,6)"), "{}", stderr(&o));
    assert_eq!(c.text(), SAMPLE);
}

#[test]
fn nth_anchor() {
    if !have_nvim() {
        return;
    }
    let c = Case::new(SAMPLE.as_bytes());
    assert!(c.run(&["@2@open(", "dd"]).status.success());
    assert!(!c.text().contains("write(data)") && c.text().contains("open(path).read()"));
}

#[test]
fn zero_match_anchor_fails() {
    if !have_nvim() {
        return;
    }
    let c = Case::new(SAMPLE.as_bytes());
    let o = c.run(&["@nothere", "dd"]);
    assert_eq!(o.status.code(), Some(1));
    assert!(stderr(&o).contains("anchor matched 0 lines"));
    assert_eq!(c.text(), SAMPLE);
}

#[test]
fn failed_search_mid_sequence_is_transactional() {
    if !have_nvim() {
        return;
    }
    let c = Case::new(SAMPLE.as_bytes());
    // The first dd succeeds in the buffer, but the failing search aborts the whole run.
    let o = c.run(&["dd", "/zzz<CR>", "dd"]);
    assert_eq!(o.status.code(), Some(1));
    assert!(stderr(&o).contains("FAILED at step 2"), "{}", stderr(&o));
    assert_eq!(c.text(), SAMPLE);
}

#[test]
fn insert_is_literal_no_autoindent() {
    if !have_nvim() {
        return;
    }
    let c = Case::new(SAMPLE.as_bytes());
    assert!(c.run(&["@data = open", "cc    with open(path) as f:<CR>        data = f.read()<Esc>"]).status.success());
    assert!(c.text().contains("    with open(path) as f:\n        data = f.read()\n    return data"), "{}", c.text());
}

#[test]
fn shift_uses_spaces_by_default_and_tabs_when_file_does() {
    if !have_nvim() {
        return;
    }
    let c = Case::new(b"def f():\n    x = 1\n");
    assert!(c.run(&["@x = 1", ">>"]).status.success());
    assert_eq!(c.text(), "def f():\n        x = 1\n");
    let t = Case::new(b"def f():\n\tx = 1\n");
    assert!(t.run(&["@x = 1", ">>"]).status.success());
    assert_eq!(t.text(), "def f():\n\t\tx = 1\n");
}

#[test]
fn preserves_crlf_and_missing_final_newline() {
    if !have_nvim() {
        return;
    }
    let c = Case::new(b"a\r\nb\r\n");
    assert!(c.run(&["ggAx<Esc>"]).status.success());
    assert_eq!(fs::read(&c.path).unwrap(), b"ax\r\nb\r\n");
    let n = Case::new(b"a\nb");
    assert!(n.run(&["ggAx<Esc>"]).status.success());
    assert_eq!(fs::read(&n.path).unwrap(), b"ax\nb");
}

#[test]
fn dry_run_does_not_write_even_as_trailing_flag() {
    if !have_nvim() {
        return;
    }
    let c = Case::new(SAMPLE.as_bytes());
    let o = c.run(&["@^def save", "dap", "--dry-run"]);
    assert!(o.status.success(), "{}", stderr(&o));
    assert!(String::from_utf8_lossy(&o.stdout).contains("(dry run, not written)"));
    assert_eq!(c.text(), SAMPLE);
}

#[test]
fn incomplete_command_warns() {
    if !have_nvim() {
        return;
    }
    let c = Case::new(SAMPLE.as_bytes());
    let o = c.run(&["d"]);
    assert!(o.status.success());
    assert!(stderr(&o).contains("had no effect"), "{}", stderr(&o));
    assert_eq!(c.text(), SAMPLE);
}

#[test]
fn double_dash_allows_steps_that_look_like_flags() {
    if !have_nvim() {
        return;
    }
    let c = Case::new(b"one\ntwo\n");
    // "-" in normal mode moves up a line; after "--" it must be treated as a step.
    let o = c.run(&["--", "G", "-", "dd"]);
    assert!(o.status.success(), "{}", stderr(&o));
    assert_eq!(c.text(), "two\n");
}

#[test]
fn usage_errors_exit_2() {
    let o = Command::new(env!("CARGO_BIN_EXE_neovain")).arg("missing.txt").arg("dd").output().unwrap();
    assert_eq!(o.status.code(), Some(2));
    let o = Command::new(env!("CARGO_BIN_EXE_neovain")).arg("only-file").output().unwrap();
    assert_eq!(o.status.code(), Some(2));
}

#[test]
fn literal_append_keeps_key_notation_and_empty_files_get_lf() {
    if !have_nvim() {
        return;
    }
    let c = Case::new(b"");
    // :0a text is literal: <del> and <Tab> must not become keys.
    let o = c.run(&[":0a\n<p>x <del>y</del> <Tab></p>\n."]);
    assert!(o.status.success(), "{}", stderr(&o));
    assert_eq!(fs::read(&c.path).unwrap(), b"<p>x <del>y</del> <Tab></p>\n");
}

/// Three blocks shaped like classes, two blank lines apart. In the order Alpha, Beta, Gamma
/// they are on lines 1-40, 43-72 and 75-124. Moving one makes a diff too long to print in full.
fn classes(order: [&str; 3]) -> String {
    let class = |name: &str| {
        let lines = [("Alpha", 40), ("Beta", 30), ("Gamma", 50)].iter().find(|(n, _)| *n == name).unwrap().1;
        let body: String = (1..lines).map(|i| format!("    {name}_{i} = {i}\n")).collect();
        format!("class {name}:\n{body}")
    };
    order.map(class).join("\n\n")
}

const IN_ORDER: [&str; 3] = ["Alpha", "Beta", "Gamma"];

fn stdout(o: &Output) -> String {
    String::from_utf8_lossy(&o.stdout).into_owned()
}

#[test]
fn long_diff_prints_a_summary_and_the_file_is_written() {
    if !have_nvim() {
        return;
    }
    let c = Case::new(classes(IN_ORDER).as_bytes());
    let o = c.run(&["@^class Beta", r":-2,/^\S/-3m$"]);
    assert!(o.status.success(), "{}", stderr(&o));
    let out = stdout(&o);
    assert!(out.contains("moved 30 lines: 43-72 -> 95-124  (class Beta:)"), "{out}");
    assert!(out.contains("  down past 50 lines: 43-92  (class Gamma:)"), "{out}");
    assert!(out.contains("  +95:class Beta:"), "{out}");
    assert!(!out.contains("@@") && !out.contains("WARNING"), "{out}");
    assert!(out.lines().count() < 20, "{out}");
    assert_eq!(c.text(), classes(["Alpha", "Gamma", "Beta"]));
}

#[test]
fn summary_warns_about_blank_lines_moved_to_the_end_of_the_file() {
    if !have_nvim() {
        return;
    }
    // The range takes the blank lines below the block, not the ones above it.
    let c = Case::new(classes(IN_ORDER).as_bytes());
    let o = c.run(&["@^class Beta", r":.,/^\S/-1m$"]);
    assert!(o.status.success(), "{}", stderr(&o));
    let out = stdout(&o);
    assert!(out.contains("moved 30 lines: 43-72 -> 93-122  (class Beta:)"), "{out}");
    assert!(out.contains("WARNING: file ends with 3 newlines, was 1 (2 blank lines at the end)"), "{out}");
    assert!(out.contains("WARNING: no blank line between 92 and 93, was 2"), "{out}");
    let text = c.text();
    assert!(text.contains("    Gamma_49 = 49\nclass Beta:\n") && text.ends_with("    Beta_29 = 29\n\n\n"), "{text}");
}

#[test]
fn summary_warns_about_an_indent_that_runs_past_the_block() {
    if !have_nvim() {
        return;
    }
    let method = |name: &str| format!("    def {name}(self):\n        {name}_1()\n        {name}_2()\n");
    let file = format!("class A:\n{}\n{}\n{}", method("first"), method("second"), method("third"));
    // Meant: the body of `first`. The range runs to the end of the file.
    let c = Case::new(file.as_bytes());
    let o = c.run(&["@def first", ":+1,$>", "--diff", "summary"]);
    assert!(o.status.success(), "{}", stderr(&o));
    let out = stdout(&o);
    assert!(out.contains("reindented 10 lines: 3-12 -> 3-12, indent +4 spaces  (first_1())"), "{out}");
    assert!(out.contains("WARNING: 2 lines are indented less than the block's first line, from +6"), "{out}");
    assert!(out.contains("  +6:        def second(self):"), "{out}");
}

#[test]
fn diff_full_prints_the_whole_diff_and_keeps_the_context_option() {
    if !have_nvim() {
        return;
    }
    let c = Case::new(classes(IN_ORDER).as_bytes());
    let o = c.run(&["--diff", "full", "-C", "0", "--dry-run", "@^class Beta", r":-2,/^\S/-3m$"]);
    assert!(o.status.success(), "{}", stderr(&o));
    let out = stdout(&o);
    assert!(out.contains("@@") && out.contains("-class Beta:") && out.contains("+class Beta:"), "{out}");
    assert!(out.contains("-    Beta_15 = 15") && out.ends_with("(dry run, not written)\n"), "{out}");
    // With no context, every line but the last is a header or a changed line.
    assert!(out.lines().all(|l| l.starts_with(['-', '+', '@']) || l == "(dry run, not written)"), "{out}");
    assert_eq!(c.text(), classes(IN_ORDER));

    let out = stdout(&c.run(&["--diff=full", "-C", "3", "--dry-run", "@^class Beta", r":-2,/^\S/-3m$"]));
    assert!(out.contains("\n     Alpha_39 = 39\n"), "{out}");
}

#[test]
fn short_diff_is_printed_in_full_unless_the_summary_is_asked_for() {
    if !have_nvim() {
        return;
    }
    // The first two lines of a diff name the file, which is another one in each case.
    let hunks = |o: &Output| stdout(o).lines().skip(2).map(|l| format!("{l}\n")).collect::<String>();
    let (auto, full) = (Case::new(SAMPLE.as_bytes()), Case::new(SAMPLE.as_bytes()));
    let out = hunks(&auto.run(&["@^def save", "dap"]));
    assert!(out.starts_with("@@ ") && out.contains("\n-def save(path, data):\n"), "{out}");
    assert_eq!(out, hunks(&full.run(&["--diff", "full", "@^def save", "dap"])));
    assert_eq!(auto.text(), full.text());

    let c = Case::new(SAMPLE.as_bytes());
    let o = c.run(&["@^def save", "dap", "--diff", "summary", "--dry-run"]);
    assert!(o.status.success(), "{}", stderr(&o));
    let out = stdout(&o);
    assert!(out.contains("+0 -3 lines in 1 hunk; 10 -> 7 lines"), "{out}");
    assert!(out.contains("deleted 2 lines: 5-6  (def save(path, data):)"), "{out}");
    assert!(!out.contains("@@") && out.ends_with("(dry run, not written)\n"), "{out}");
    assert_eq!(c.text(), SAMPLE);
}

#[test]
fn summary_does_not_change_failures_or_warnings() {
    if !have_nvim() {
        return;
    }
    let c = Case::new(classes(IN_ORDER).as_bytes());
    let o = c.run(&["--diff", "summary", "@^class Beta", r":-2,/^\S/-3m$", "@nothere", "dd"]);
    assert_eq!(o.status.code(), Some(1));
    assert!(stderr(&o).contains("FAILED at step 3 \"@nothere\": anchor matched 0 lines"), "{}", stderr(&o));
    assert!(stdout(&o).is_empty());
    assert_eq!(c.text(), classes(IN_ORDER));

    let o = c.run(&["--diff", "summary", "d"]);
    assert!(o.status.success());
    assert!(stderr(&o).contains("had no effect"), "{}", stderr(&o));
    assert_eq!(stdout(&o), "no change (cursor ended on line 1)\n");
}

#[test]
fn bad_diff_option_is_a_usage_error() {
    let o = Command::new(env!("CARGO_BIN_EXE_neovain")).args(["--diff", "short", "f.py", "dd"]).output().unwrap();
    assert_eq!(o.status.code(), Some(2));
    assert!(stderr(&o).contains("bad value for --diff: \"short\""), "{}", stderr(&o));
    let o = Command::new(env!("CARGO_BIN_EXE_neovain")).args(["f.py", "dd", "--diff"]).output().unwrap();
    assert_eq!(o.status.code(), Some(2));
    assert!(stderr(&o).contains("--diff needs a value"), "{}", stderr(&o));
}
