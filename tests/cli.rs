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
