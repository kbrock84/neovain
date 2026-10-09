//! The agent-safe profile (--safe / NEOVAIN_SAFE=1 with a workspace root): what it rejects
//! before Neovim starts, that a valid edit inside the workspace still works, and that the
//! default and ex-only modes are untouched. Tests that edit skip if nvim is missing.

use std::fs;
use std::path::PathBuf;
use std::process::{Command, Output};
use std::sync::atomic::{AtomicUsize, Ordering};

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

static NEXT: AtomicUsize = AtomicUsize::new(0);

/// A workspace with a file inside it and a second file outside it.
struct Case {
    dir: PathBuf,
    root: PathBuf,
    path: PathBuf,
    outside: PathBuf,
}

impl Case {
    fn new() -> Self {
        let n = NEXT.fetch_add(1, Ordering::Relaxed);
        let dir = std::env::temp_dir().join(format!("neovain-safe-test-{}-{n}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        let root = dir.join("ws");
        fs::create_dir_all(&root).unwrap();
        let path = root.join("app.py");
        fs::write(&path, SAMPLE).unwrap();
        let outside = dir.join("outside.py");
        fs::write(&outside, SAMPLE).unwrap();
        Self { dir, root, path, outside }
    }
    fn text(&self) -> String {
        fs::read_to_string(&self.path).unwrap()
    }
}

impl Drop for Case {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.dir);
    }
}

fn neovain() -> Command {
    let mut cmd = Command::new(env!("CARGO_BIN_EXE_neovain"));
    cmd.env_remove("NEOVAIN_SAFE").env_remove("NEOVAIN_WORKSPACE").env_remove("NEOVAIN_EX_ONLY").env_remove("MSYSTEM");
    cmd
}

fn stderr(o: &Output) -> String {
    String::from_utf8_lossy(&o.stderr).to_string()
}

#[test]
fn a_valid_edit_inside_the_workspace_succeeds() {
    if !have_nvim() {
        return;
    }
    let c = Case::new();
    let o = neovain()
        .args(["--safe", "--workspace"])
        .arg(&c.root)
        .arg(&c.path)
        .args(["@^def save", ":g/^def load/d", ":%s/write(data)/write(payload)/"])
        .output()
        .unwrap();
    assert!(o.status.success(), "{}", stderr(&o));
    let text = c.text();
    assert!(text.contains("def save(path, data)") && !text.contains("def load(path)"), "{text}");
    assert!(text.contains("write(payload)"), "{text}");
}

#[test]
fn execution_keys_and_files_are_refused_before_neovim() {
    let c = Case::new();
    let marker = c.dir.join("MARKER");
    for step in [
        format!(":call system('touch {}')", marker.display()),
        format!(":!touch {}", marker.display()),
        format!(":execute '!touch {}'", marker.display()),
        format!(":lua vim.fn.writefile({{1}}, '{}')", marker.display()),
        format!(":source {}", marker.display()),
        ":normal ZZ".to_string(),
        ":read /etc/passwd".to_string(),
        format!(":s/x/\\=system('touch {}')/", marker.display()),
        "dd".to_string(),
        "ciwnew<Esc>".to_string(),
        "ZZ".to_string(),
    ] {
        let o = neovain().args(["--safe", "--workspace"]).arg(&c.root).arg(&c.path).arg(&step).output().unwrap();
        assert_eq!(o.status.code(), Some(2), "{step}: {}", stderr(&o));
        // Rejected either by the profile or by the write/quit rule, both before Neovim.
        let err = stderr(&o);
        assert!(err.contains("safe profile") || err.contains("write the file or quit"), "{step}: {err}");
        assert!(!marker.exists(), "{step} reached neovim anyway");
        assert_eq!(c.text(), SAMPLE, "{step}");
    }
}

#[test]
fn the_expression_register_is_refused_in_every_form() {
    // The re-audit's class: `=` evaluates vimscript, with or without a bang, a space, a
    // modifier or a :g/:v nest around it. Rejected before Neovim starts.
    let c = Case::new();
    for step in [
        ":put =system('id')",
        ":put! =1",
        ":put!=system('id')",
        ":pu!! =1",
        ":silent put! =system('id')",
        ":silent! put =1",
        ":keepjumps put! =1",
        ":g/^def/put! =system('id')",
        ":v/^def/put =1",
    ] {
        let o = neovain().args(["--safe", "--workspace"]).arg(&c.root).arg(&c.path).arg(step).output().unwrap();
        assert_eq!(o.status.code(), Some(2), "{step}: {}", stderr(&o));
        assert!(stderr(&o).contains("expression register"), "{step}: {}", stderr(&o));
        assert_eq!(c.text(), SAMPLE, "{step}");
    }
    // A normal register with the bang is legitimate: it reaches Neovim, which reports the
    // empty register instead of the profile refusing the step.
    if have_nvim() {
        let o = neovain().args(["--safe", "--workspace"]).arg(&c.root).arg(&c.path).arg(":put! a").output().unwrap();
        assert_eq!(o.status.code(), Some(1), "{}", stderr(&o));
        assert!(stderr(&o).contains("E353"), "{}", stderr(&o));
        assert_eq!(c.text(), SAMPLE);
    }
}

#[test]
fn the_profile_needs_a_workspace_root() {
    let c = Case::new();
    let o = neovain().arg("--safe").arg(&c.path).arg(":%s/a/b/").output().unwrap();
    assert_eq!(o.status.code(), Some(2), "{}", stderr(&o));
    assert!(stderr(&o).contains("workspace"), "{}", stderr(&o));
    let o = neovain().env("NEOVAIN_SAFE", "1").arg(&c.path).arg(":%s/a/b/").output().unwrap();
    assert_eq!(o.status.code(), Some(2), "{}", stderr(&o));
    assert!(stderr(&o).contains("workspace"), "{}", stderr(&o));
    assert_eq!(c.text(), SAMPLE);
}

#[test]
fn targets_outside_the_workspace_are_refused() {
    let c = Case::new();
    for file in [&c.outside, &c.root.join("..").join("outside.py")] {
        // With the profile, and with confinement alone: the two controls are independent.
        for extra in [vec!["--safe"], vec![]] {
            let mut args: Vec<&str> = vec!["--workspace"];
            let root = c.root.to_str().unwrap();
            args.push(root);
            args.extend(&extra);
            args.push(file.to_str().unwrap());
            args.push(":%s/a/b/");
            let o = neovain().args(&args).output().unwrap();
            assert_eq!(o.status.code(), Some(2), "{file:?} {extra:?}: {}", stderr(&o));
            assert!(stderr(&o).contains("outside the workspace"), "{file:?}: {}", stderr(&o));
        }
        assert_eq!(fs::read_to_string(file).unwrap(), SAMPLE, "{file:?} was touched");
    }
}

#[cfg(unix)]
#[test]
fn a_symlink_that_leaves_the_workspace_is_refused() {
    let c = Case::new();
    let link = c.root.join("link.py");
    std::os::unix::fs::symlink(&c.outside, &link).unwrap();
    let o = neovain().args(["--safe", "--workspace"]).arg(&c.root).arg(&link).arg(":%s/a/b/").output().unwrap();
    assert_eq!(o.status.code(), Some(2), "{}", stderr(&o));
    assert!(stderr(&o).contains("outside the workspace"), "{}", stderr(&o));
    assert_eq!(fs::read_to_string(&c.outside).unwrap(), SAMPLE);
}

#[test]
fn a_failing_step_still_leaves_the_file_unchanged() {
    if !have_nvim() {
        return;
    }
    let c = Case::new();
    let o = neovain()
        .args(["--safe", "--workspace"])
        .arg(&c.root)
        .arg(&c.path)
        .args(["@^def save", "@^nope"])
        .output()
        .unwrap();
    assert_eq!(o.status.code(), Some(1), "{}", stderr(&o));
    assert!(stderr(&o).contains("FAILED at step 2"), "{}", stderr(&o));
    assert_eq!(c.text(), SAMPLE);
}

#[test]
fn the_environment_interface_matches_the_flags() {
    if !have_nvim() {
        return;
    }
    let c = Case::new();
    let root = c.root.to_str().unwrap();
    let file = c.path.to_str().unwrap();
    // NEOVAIN_SAFE=1 + NEOVAIN_WORKSPACE does what --safe --workspace does: edit, and refuse.
    let o = Command::new(env!("CARGO_BIN_EXE_neovain"))
        .env("NEOVAIN_SAFE", "1")
        .env("NEOVAIN_WORKSPACE", root)
        .env_remove("MSYSTEM")
        .args([file, "@^def save", ":%s/def save/def store/"])
        .output()
        .unwrap();
    assert!(o.status.success(), "{}", stderr(&o));
    assert!(c.text().contains("def store"), "{}", c.text());
    let o = Command::new(env!("CARGO_BIN_EXE_neovain"))
        .env("NEOVAIN_SAFE", "1")
        .env("NEOVAIN_WORKSPACE", root)
        .env_remove("MSYSTEM")
        .args([file, ":!touch nope"])
        .output()
        .unwrap();
    assert_eq!(o.status.code(), Some(2), "{}", stderr(&o));
    assert!(!c.root.join("nope").exists());
}

#[test]
fn without_the_flags_nothing_changed() {
    if !have_nvim() {
        return;
    }
    let c = Case::new();
    // The default mode still edits with normal-mode keys, with no workspace in sight...
    let o = neovain().arg(&c.path).args(["@^def load", "wciwread_file<Esc>"]).output().unwrap();
    assert!(o.status.success(), "{}", stderr(&o));
    assert!(c.text().contains("def read_file(path):"), "{}", c.text());
    // ...and --workspace alone confines without turning the allow-list on.
    fs::write(&c.path, SAMPLE).unwrap();
    let o = neovain().arg("--workspace").arg(&c.root).arg(&c.path).args(["@^def load", "dd"]).output().unwrap();
    assert!(o.status.success(), "{}", stderr(&o));
    assert!(!c.text().contains("def load(path)"), "{}", c.text());
}

#[test]
fn the_help_documents_the_profile() {
    let o = neovain().arg("--help").output().unwrap();
    let help = String::from_utf8_lossy(&o.stdout).to_string();
    for want in ["--safe", "--workspace", "NEOVAIN_SAFE", "NEOVAIN_WORKSPACE"] {
        assert!(help.contains(want), "help does not mention {want}:\n{help}");
    }
}
