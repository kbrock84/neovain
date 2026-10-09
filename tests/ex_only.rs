//! ex-only mode (NEOVAIN_EX_ONLY=1): what it rejects, and what it still allows.
//! Requires `nvim` on PATH (or NEOVAIN_NVIM); the test is skipped if it is missing.

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
    dir: PathBuf,
    path: PathBuf,
}

impl Case {
    fn new(bytes: &[u8]) -> Self {
        let n = std::process::id();
        let dir = std::env::temp_dir().join(format!("neovain-exonly-test-{n}"));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        let path = dir.join("app.py");
        fs::write(&path, bytes).unwrap();
        Self { dir, path }
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

fn stderr(o: &Output) -> String {
    String::from_utf8_lossy(&o.stderr).to_string()
}

#[test]
fn ex_only_rejects_commands_that_leave_the_buffer() {
    if !have_nvim() {
        return;
    }
    let c = Case::new(SAMPLE.as_bytes());
    let run = |step: String| {
        Command::new(env!("CARGO_BIN_EXE_neovain"))
            .arg(&c.path)
            .arg(&step)
            .env("NEOVAIN_EX_ONLY", "1")
            .env_remove("MSYSTEM")
            .output()
            .unwrap()
    };
    let shell = c.path.with_file_name("SHELL_RAN");
    let lua = c.path.with_file_name("LUA_RAN");
    for (step, marker) in [
        (format!(":!touch {}", shell.display()), shell),
        (format!(":lua vim.fn.writefile({{1}}, '{}')", lua.display()), lua),
    ] {
        let o = run(step.clone());
        assert_eq!(o.status.code(), Some(1), "{step}: {}", stderr(&o));
        assert!(stderr(&o).contains("NEOVAIN_EX_ONLY"), "{step}: {}", stderr(&o));
        assert!(!marker.exists(), "{step} reached the shell anyway");
        assert_eq!(c.text(), SAMPLE, "{step}");
    }
    for step in [":python pass", ":source nope.vim", ":runtime nope.vim", ":earlier", ":normal dd"] {
        let o = run(step.into());
        assert_eq!(o.status.code(), Some(1), "{step}: {}", stderr(&o));
        assert_eq!(c.text(), SAMPLE, "{step}");
    }
    // Still an editor: an ex command that stays inside Neovim is allowed.
    let o = run(":call append(0, ['added'])".into());
    assert!(o.status.success(), "{}", stderr(&o));
    assert!(c.text().starts_with("added\n"), "{}", c.text());
}
