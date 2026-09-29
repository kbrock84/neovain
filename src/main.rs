//! neovain: apply vim keystrokes / ex commands to a file with headless Neovim, print a diff.
//!
//! The whole step sequence runs in one Neovim process. The file is only written if every step
//! succeeds; the first failing step aborts the run and leaves the file untouched.

mod summary;

use serde_json::{json, Value};
use similar::{DiffTag, TextDiff};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode, Stdio};
use std::time::{Duration, Instant};
use std::{env, fs, thread};

const DRIVER: &str = include_str!("driver.lua");

const USAGE: &str = "\
neovain: transactional vim editing for agents

usage: neovain [OPTIONS] FILE STEP [STEP ...]

Steps (applied in order, stopping at the first failure; file untouched on failure):
  @regex       move cursor to the ONE line matching regex (vim regex; fails on 0 or >1 matches)
  @N@regex     move cursor to the Nth matching line
  :excmd       run an ex command, e.g. ':%s/foo/bar/g'  ':g/^#/d'  ':10,20m$'
  anything     normal-mode keys, <Esc>/<CR>/<C-v> notation, e.g. 'ciwnewname<Esc>'

Options:
  -n, --dry-run      show the changes without writing
  -C, --context N    diff context lines (default 2)
      --diff MODE    what to print. auto (default): the diff if it is short, a summary of the
                     changes if it is long. full: always the diff. summary: always the summary
      --sw N         shiftwidth for > and < when the file indents with spaces (default 4)
      --timeout SECS default 10
  --                 treat everything after as steps (for steps starting with '-')
  -h, --help         show this help
  -V, --version      show version

Environment:
  NEOVAIN_NVIM       path to the nvim binary (default: nvim on PATH)
  NEOVAIN_EX_ONLY=1  allow only @anchor and :ex steps (no normal-mode keys, no :normal)

Exit status: 0 success, 1 a step failed or timed out (file unchanged), 2 usage/setup error.
";

struct Args {
    file: PathBuf,
    steps: Vec<String>,
    dry_run: bool,
    diff: Mode,
    context: usize,
    sw: u32,
    timeout: Duration,
}

/// What to print after an edit that changed the file.
#[derive(Clone, Copy, PartialEq)]
enum Mode {
    /// The diff if it is short, the summary if it is long.
    Auto,
    Full,
    Summary,
}

impl std::str::FromStr for Mode {
    type Err = ();
    fn from_str(s: &str) -> Result<Self, ()> {
        match s {
            "auto" => Ok(Mode::Auto),
            "full" => Ok(Mode::Full),
            "summary" => Ok(Mode::Summary),
            _ => Err(()),
        }
    }
}

enum Fail {
    Usage(String),
    Step(String),
}

fn parse_args() -> Result<Option<Args>, String> {
    let mut it = env::args().skip(1);
    let (mut dry_run, mut context, mut sw, mut timeout) = (false, 2usize, 4u32, 10.0f64);
    let mut diff = Mode::Auto;
    let mut positional = Vec::new();
    let mut only_steps = false;
    fn value<T: std::str::FromStr>(flag: &str, v: Option<String>) -> Result<T, String> {
        let v = v.ok_or_else(|| format!("{flag} needs a value"))?;
        v.parse().map_err(|_| format!("bad value for {flag}: {v:?}"))
    }
    while let Some(a) = it.next() {
        // Known flags are accepted anywhere (agents often append them), so a trailing
        // `--dry-run` is never typed as normal-mode keys. Anything after `--` is a step.
        if only_steps {
            positional.push(a);
            continue;
        }
        match a.as_str() {
            "--" => only_steps = true,
            "-h" | "--help" => {
                print!("{USAGE}");
                return Ok(None);
            }
            "-V" | "--version" => {
                println!("neovain {}", env!("CARGO_PKG_VERSION"));
                return Ok(None);
            }
            "-n" | "--dry-run" => dry_run = true,
            "-C" | "--context" => context = value(&a, it.next())?,
            "--sw" => sw = value(&a, it.next())?,
            "--timeout" => timeout = value(&a, it.next())?,
            "--diff" => diff = value(&a, it.next())?,
            s if s.starts_with("--diff=") => diff = value("--diff", s.split_once('=').map(|(_, v)| v.to_string()))?,
            s if s.starts_with("--") => return Err(format!("unknown option {s}")),
            _ => positional.push(a),
        }
    }
    if positional.len() < 2 {
        return Err("need FILE and at least one STEP (see --help)".into());
    }
    let file = PathBuf::from(positional.remove(0));
    Ok(Some(Args { file, steps: positional, dry_run, diff, context, sw, timeout: Duration::from_secs_f64(timeout) }))
}

/// Git Bash/MSYS rewrites args like '/foo<CR>' into 'C:/Program Files/Git/foo<CR>' before we see them.
fn msys_mangled(step: &str) -> bool {
    let b = step.as_bytes();
    (1..b.len().saturating_sub(1)).any(|i| b[i] == b':' && b[i + 1] == b'/' && b[i - 1].is_ascii_alphabetic())
        && step.contains("/Git/")
}

/// In ex-only mode, reject steps that type normal-mode keys, directly or via :normal / :exe.
fn ex_only_violation(step: &str) -> Option<&'static str> {
    if step.starts_with('@') {
        return None;
    }
    let Some(cmd) = step.strip_prefix(':') else {
        return Some("normal-mode keys are disabled (NEOVAIN_EX_ONLY); use @anchor and :ex steps");
    };
    // Strip a leading range like "%", "'<,'>", "10,20", ".,+3" so ":%norm" is caught too.
    let body = cmd.trim_start_matches(|c: char| c.is_ascii_digit() || ",.;$%'<>+-/?^ ".contains(c));
    let word: String = body.chars().take_while(|c| c.is_ascii_alphabetic()).collect();
    let is_normal = word.len() >= 4 && "normal".starts_with(&word);
    let is_exe = word.len() >= 3 && "execute".starts_with(&word);
    let in_global = (word.starts_with('g') || word.starts_with('v'))
        && ("global".starts_with(&word) || "vglobal".starts_with(&word))
        && body.contains("norm");
    (is_normal || is_exe || in_global).then_some(":normal/:execute are disabled (NEOVAIN_EX_ONLY)")
}

fn run_nvim(a: &Args, file: &Path) -> Result<(Value, Option<Vec<u8>>), Fail> {
    let tmp = tempfile::Builder::new().prefix("neovain-").tempdir().map_err(|e| Fail::Usage(e.to_string()))?;
    let (driver, job_path, out, report) =
        (tmp.path().join("driver.lua"), tmp.path().join("job.json"), tmp.path().join("out"), tmp.path().join("report.json"));
    let job = json!({"file": file, "steps": a.steps, "sw": a.sw, "out": out, "report": report});
    fs::write(&driver, DRIVER).and_then(|_| fs::write(&job_path, job.to_string())).map_err(|e| Fail::Usage(e.to_string()))?;

    let nvim = env::var_os("NEOVAIN_NVIM").unwrap_or_else(|| "nvim".into());
    let mut child = Command::new(&nvim)
        .args(["--clean", "--headless", "-n", "-i", "NONE", "-l"])
        .arg(&driver)
        .arg(&job_path)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| Fail::Usage(format!("cannot run {}: {e} (is Neovim installed?)", nvim.to_string_lossy())))?;

    let start = Instant::now();
    let status = loop {
        if let Some(s) = child.try_wait().map_err(|e| Fail::Usage(e.to_string()))? {
            break s;
        }
        if start.elapsed() > a.timeout {
            let _ = child.kill();
            let _ = child.wait();
            return Err(Fail::Step(format!(
                "timed out after {:?} (a step is probably waiting for input); file unchanged",
                a.timeout
            )));
        }
        thread::sleep(Duration::from_millis(2));
    };

    let report: Value = match fs::read_to_string(&report) {
        Ok(s) => serde_json::from_str(&s).map_err(|e| Fail::Usage(format!("bad report from nvim: {e}")))?,
        Err(_) => {
            let mut stderr = String::new();
            if let Some(mut e) = child.stderr.take() {
                let _ = std::io::Read::read_to_string(&mut e, &mut stderr);
            }
            return Err(Fail::Usage(format!("nvim produced no report (exit {status}):\n{stderr}")));
        }
    };
    let after = if report["ok"].as_bool() == Some(true) {
        Some(fs::read(&out).map_err(|e| Fail::Usage(e.to_string()))?)
    } else {
        None
    };
    Ok((report, after))
}

fn last_line(report: &Value) -> i64 {
    report["steps"].as_array().and_then(|s| s.last()).and_then(|s| s["line"].as_i64()).unwrap_or(1)
}

fn write_atomic(path: &Path, data: &[u8]) -> std::io::Result<()> {
    let mut name = path.file_name().unwrap_or_default().to_os_string();
    name.push(".neovain~");
    let tmp = path.with_file_name(name);
    fs::write(&tmp, data)?;
    if let Ok(meta) = fs::metadata(path) {
        let _ = fs::set_permissions(&tmp, meta.permissions());
    }
    fs::rename(&tmp, path)
}

fn run(a: Args) -> Result<(), Fail> {
    if !a.file.is_file() {
        return Err(Fail::Usage(format!("no such file: {}", a.file.display())));
    }
    if env::var_os("MSYSTEM").is_some() {
        if let Some(s) = a.steps.iter().find(|s| msys_mangled(s)) {
            return Err(Fail::Usage(format!(
                "step {s:?} looks mangled by MSYS path conversion; rerun with MSYS_NO_PATHCONV=1"
            )));
        }
    }
    if env::var("NEOVAIN_EX_ONLY").is_ok_and(|v| v == "1") {
        for (i, s) in a.steps.iter().enumerate() {
            if let Some(why) = ex_only_violation(s) {
                return Err(Fail::Step(format!("FAILED at step {} {s:?}: {why}\nfile unchanged", i + 1)));
            }
        }
    }
    let before = fs::read(&a.file).map_err(|e| Fail::Usage(e.to_string()))?;
    let abs = fs::canonicalize(&a.file).map_err(|e| Fail::Usage(e.to_string()))?;
    // canonicalize on Windows yields \\?\C:\..., which nvim can't open; strip the verbatim prefix.
    let abs = PathBuf::from(abs.to_string_lossy().trim_start_matches(r"\\?\").to_string());
    let (report, after) = run_nvim(&a, &abs)?;

    let Some(after) = after else {
        let i = report["failed"].as_u64().unwrap_or(0) as usize;
        let step = a.steps.get(i.wrapping_sub(1)).map(String::as_str).unwrap_or("?");
        return Err(Fail::Step(format!(
            "FAILED at step {i} {step:?}: {}\ncursor was on line {}; file unchanged",
            report["error"].as_str().unwrap_or("unknown error"),
            last_line(&report)
        )));
    };

    let mut err = std::io::stderr().lock();
    for (i, st) in report["steps"].as_array().into_iter().flatten().enumerate() {
        let s = st["step"].as_str().unwrap_or("");
        if !s.starts_with(['@', ':']) && st["changed"] == false && st["moved"] == false {
            let _ = writeln!(err, "warning: step {} {s:?} had no effect (incomplete or invalid command?)", i + 1);
        }
    }

    let mut out = std::io::stdout().lock();
    if after == before {
        let _ = writeln!(out, "no change (cursor ended on line {})", last_line(&report));
        return Ok(());
    }
    let (old, new) = (String::from_utf8_lossy(&before), String::from_utf8_lossy(&after));
    let name = a.file.display().to_string();
    let diff = TextDiff::from_lines(old.as_ref(), new.as_ref());
    let full = diff.unified_diff().context_radius(a.context).header(&name, &name).to_string();
    let (added, removed) = diff.ops().iter().filter(|op| op.tag() != DiffTag::Equal).fold((0, 0), |(added, removed), op| {
        (added + op.new_range().len(), removed + op.old_range().len())
    });
    let short = added + removed <= summary::FULL_DIFF_MAX_CHANGED && full.lines().count() <= summary::OUTPUT_MAX_LINES;
    if a.diff == Mode::Full || (a.diff == Mode::Auto && short) {
        let _ = write!(out, "{full}");
    } else {
        let totals = summary::Totals { added, removed, hunks: diff.grouped_ops(a.context).len() };
        let _ = write!(out, "{}", summary::render(&summary::analyze(&old, &new), &name, &totals));
    }
    if a.dry_run {
        let _ = writeln!(out, "(dry run, not written)");
    } else {
        write_atomic(&a.file, &after).map_err(|e| Fail::Usage(format!("write failed: {e}")))?;
    }
    Ok(())
}

fn main() -> ExitCode {
    let args = match parse_args() {
        Ok(Some(a)) => a,
        Ok(None) => return ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("neovain: {e}");
            return ExitCode::from(2);
        }
    };
    match run(args) {
        Ok(()) => ExitCode::SUCCESS,
        Err(Fail::Step(m)) => {
            eprintln!("{m}");
            ExitCode::from(1)
        }
        Err(Fail::Usage(m)) => {
            eprintln!("neovain: {m}");
            ExitCode::from(2)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{ex_only_violation, msys_mangled};

    #[test]
    fn ex_only_rules() {
        for ok in ["@^def", ":%s/a/b/g", ":g/# DEBUG$/d", ":10,20m$", ":call append(3, ['x'])", ":n", ":nohl"] {
            assert!(ex_only_violation(ok).is_none(), "{ok}");
        }
        for bad in ["dd", "ciwx<Esc>", ":norm dd", ":normal! dd", ":%norm A;", ":'<,'>normal x", ":exe \"norm dd\"",
            ":g/x/norm dd", ":v/x/normal dd"] {
            assert!(ex_only_violation(bad).is_some(), "{bad}");
        }
    }

    #[test]
    fn detects_msys_mangling() {
        assert!(msys_mangled("C:/Program Files/Git/foo<CR>"));
        assert!(msys_mangled("@C:/Program Files/Git/^def"));
        assert!(!msys_mangled(":%s/a:/b/g"));
        assert!(!msys_mangled("/foo<CR>"));
    }
}
