//! The change summary: what an edit did, in a bounded number of lines.
//!
//! Agents do not read a 2,000-line diff, so above a size threshold neovain prints this instead.
//! It reports moved, deleted, inserted and re-indented blocks, token replacements that repeat,
//! and blank-line damage where blocks were joined. It works on lines and parses no language.
//!
//! How it works:
//! 1. Blank lines are set aside. Blocks are found among the other lines, and the blank lines
//!    are compared afterwards, gap by gap. A block's range therefore never depends on which
//!    of two equal blank lines a diff happens to pick.
//! 2. Token replacements that repeat (`log_event` -> `emit_event`) are found first and then
//!    count as equal, so a renamed line inside a moved block does not split the block.
//! 3. Lines are compared without their leading whitespace. A line that is the only one of its
//!    kind in both files anchors a run of lines that the files share. One run has one change
//!    of indentation.
//! 4. Of those runs, the heaviest set that kept its order stayed. The others moved.
//! 5. Common lines at the ends of a run join the run they touch. The lines still unclaimed are
//!    aligned with an ordinary diff, between the runs that stayed.

use similar::{capture_diff_slices_deadline, Algorithm, DiffOp};
use std::collections::HashMap;
use std::ops::Range;
use std::time::{Duration, Instant};

/// In `auto` mode the diff is printed in full if it changes at most this many lines...
pub const FULL_DIFF_MAX_CHANGED: usize = 60;
/// ...and is no longer than this. The summary is never longer either, so in `auto` mode the
/// output has at most this many lines, whatever the size of the change.
pub const OUTPUT_MAX_LINES: usize = 80;

/// A token replacement is looked for in the whole file once it explains this many changed lines.
const REPEAT_MIN: usize = 3;
const MAX_REPLACEMENTS: usize = 5;
/// Moving one more block costs more than moving any number of lines: see `weight`.
const HEAD_WEIGHT: u64 = 1 << 24;
/// A run of at most this many rows between two larger changes counts as part of the change.
const ISLAND_MAX: usize = 2;
const MAX_WARNINGS: usize = 8;
const MAX_NOTES: usize = 4;
/// Excerpt lines are cut at this many characters.
const MAX_WIDTH: usize = 100;
const DIFF_TIME: Duration = Duration::from_secs(3);

const LEGEND: &str = "summary, not the diff (--diff full prints it). \
-N: line N of the old file. +N: line N of the new file. N: the line next to the block, new file.";

/// First and last line of a block, counted from 1.
pub type Span = (usize, usize);

/// How the leading whitespace of a block changed.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Indent<'a> {
    Same,
    Add(&'a str),
    Remove(&'a str),
    Other,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Moved,
    Reindented,
    Deleted,
    Inserted,
    Changed,
}

/// A run of lines that changed together. Spans start and end on a line that is not blank.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Block<'a> {
    pub kind: Kind,
    pub old: Option<Span>,
    pub new: Option<Span>,
    pub indent: Indent<'a>,
    /// The line of the new file that the block follows now. 0 is the start of the file.
    pub after: usize,
    /// Lines indented less than the block's first line: how many, and the first of them.
    pub outdented: Option<(usize, usize)>,
    /// For a block that moved: true if it went down, and the lines it passed, which stayed.
    pub passed: Option<(bool, Span)>,
}

/// One token replaced by another on many lines.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Replacement {
    pub from: String,
    pub to: String,
    pub lines: usize,
    /// Lines of the new file that still contain `from`.
    pub left: usize,
}

/// A run of blank lines whose length changed.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Spacing {
    /// True where blocks were joined and the run fits neither of them.
    pub warn: bool,
    /// The line of the new file above the run. 0 is the start of the file.
    pub above: usize,
    pub now: usize,
    /// The blank lines that followed the line above and preceded the line below, before.
    pub was: Vec<usize>,
}

pub struct Analysis<'a> {
    pub old: Vec<&'a str>,
    pub new: Vec<&'a str>,
    pub replacements: Vec<Replacement>,
    pub blocks: Vec<Block<'a>>,
    pub spacing: Vec<Spacing>,
    /// Newlines at the end of the file, before and after.
    pub end_newlines: (usize, usize),
}

/// The counts of the full diff, for the first line of the summary.
pub struct Totals {
    pub added: usize,
    pub removed: usize,
    pub hunks: usize,
}

/// A line that is not blank.
#[derive(Clone, Copy)]
struct Row<'a> {
    no: usize,
    text: &'a str,
    body: &'a str,
    width: usize,
}

/// Which old row is which new row, and whether the pair stayed in place.
struct Map {
    o2n: Vec<Option<usize>>,
    n2o: Vec<Option<usize>>,
    stays: Vec<bool>,
}

impl Map {
    fn pair(&mut self, i: usize, j: usize) {
        self.o2n[i] = Some(j);
        self.n2o[j] = Some(i);
    }
}

/// A claimed run: `len` rows from `old` on are the rows from `new` on.
struct Tile {
    old: usize,
    new: usize,
    len: usize,
}

fn split_lines(s: &str) -> Vec<&str> {
    let mut lines: Vec<&str> = s.split('\n').collect();
    if lines.last().is_some_and(|l| l.is_empty()) {
        lines.pop();
    }
    lines.into_iter().map(|l| l.strip_suffix('\r').unwrap_or(l)).collect()
}

pub fn trailing_newlines(s: &str) -> usize {
    let (mut rest, mut count) = (s, 0);
    while let Some(r) = rest.strip_suffix("\r\n").or_else(|| rest.strip_suffix('\n')) {
        rest = r;
        count += 1;
    }
    count
}

fn lead(s: &str) -> &str {
    &s[..s.len() - s.trim_start().len()]
}

fn width(s: &str) -> usize {
    lead(s).chars().map(|c| if c == '\t' { 8 } else { 1 }).sum()
}

fn rows<'a>(lines: &[&'a str]) -> Vec<Row<'a>> {
    let row = |(i, l): (usize, &&'a str)| Row { no: i + 1, text: l, body: l.trim_start(), width: width(l) };
    lines.iter().enumerate().filter(|(_, l)| !l.trim().is_empty()).map(row).collect()
}

fn shift<'a>(old: &'a str, new: &'a str) -> Indent<'a> {
    let (a, b) = (lead(old), lead(new));
    if a == b {
        Indent::Same
    } else if let Some(more) = b.strip_prefix(a).or_else(|| b.strip_suffix(a)) {
        Indent::Add(more)
    } else if let Some(less) = a.strip_prefix(b).or_else(|| a.strip_suffix(b)) {
        Indent::Remove(less)
    } else {
        Indent::Other
    }
}

/// Words, runs of whitespace, and every other character on its own.
fn tokens(s: &str) -> Vec<&str> {
    fn class(c: char) -> u8 {
        if c.is_alphanumeric() || c == '_' {
            0
        } else if c.is_whitespace() {
            1
        } else {
            2
        }
    }
    let (mut out, mut start, mut last) = (Vec::new(), 0, 2);
    for (i, c) in s.char_indices() {
        let k = class(c);
        if i > start && (k != last || k == 2) {
            out.push(&s[start..i]);
            start = i;
        }
        last = k;
    }
    if start < s.len() {
        out.push(&s[start..]);
    }
    out
}

/// The one token replacement that turns `old` into `new`, if there is one.
fn token_swap<'a>(old: &'a str, new: &'a str) -> Option<(&'a str, &'a str)> {
    let (a, b) = (tokens(old), tokens(new));
    if a.len() != b.len() {
        return None;
    }
    let mut found = None;
    for (x, y) in a.into_iter().zip(b) {
        if x == y {
            continue;
        }
        if x.trim().is_empty() || y.trim().is_empty() || found.is_some_and(|f| f != (x, y)) {
            return None;
        }
        found = Some((x, y));
    }
    found
}

fn intern<'k>(ids: &mut HashMap<std::borrow::Cow<'k, str>, u32>, key: std::borrow::Cow<'k, str>) -> u32 {
    let next = ids.len() as u32;
    *ids.entry(key).or_insert(next)
}

/// A stretch of an ordinary diff where the two sides differ: the rows of the old side and of the new.
type Gap = (Range<usize>, Range<usize>);

/// An ordinary diff: the pairs of equal items, and the stretches between them.
fn align(a: &[u32], b: &[u32], deadline: Instant) -> (Vec<(usize, usize)>, Vec<Gap>) {
    let (mut pairs, mut gaps) = (Vec::new(), Vec::new());
    let mut open: Option<Gap> = None;
    for op in capture_diff_slices_deadline(Algorithm::Myers, a, b, Some(deadline)) {
        if let DiffOp::Equal { old_index, new_index, len } = op {
            gaps.extend(open.take());
            pairs.extend((0..len).map(|k| (old_index + k, new_index + k)));
        } else if let Some((old, new)) = &mut open {
            old.end = op.old_range().end;
            new.end = op.new_range().end;
        } else {
            open = Some((op.old_range(), op.new_range()));
        }
    }
    gaps.extend(open);
    (pairs, gaps)
}

/// Token replacements that explain at least `REPEAT_MIN` changed lines, most frequent first.
fn discover<'a>(o: &[Row<'a>], n: &[Row<'a>], deadline: Instant) -> Vec<(&'a str, &'a str)> {
    let mut ids = HashMap::new();
    let a: Vec<u32> = o.iter().map(|r| intern(&mut ids, r.text.into())).collect();
    let b: Vec<u32> = n.iter().map(|r| intern(&mut ids, r.text.into())).collect();
    let mut count: HashMap<(&str, &str), usize> = HashMap::new();
    for (old, new) in align(&a, &b, deadline).1 {
        let (x, y) = (&o[old], &n[new]);
        let mut swap = |p: &Row<'a>, q: &Row<'a>| {
            let found = token_swap(p.body, q.body);
            if let Some(s) = found {
                *count.entry(s).or_default() += 1;
            }
            found.is_some()
        };
        if x.len() == y.len() {
            for (p, q) in x.iter().zip(y) {
                swap(p, q);
            }
            continue;
        }
        // Next to a larger change, the replaced lines are at the start or the end of the stretch.
        let most = x.len().min(y.len());
        let front = (0..most).take_while(|&k| swap(&x[k], &y[k])).count();
        let _ = (1..=most - front).take_while(|&k| swap(&x[x.len() - k], &y[y.len() - k])).count();
    }
    let mut found: Vec<_> = count.into_iter().filter(|(_, c)| *c >= REPEAT_MIN).collect();
    found.sort_by(|a, b| b.1.cmp(&a.1).then(a.0.cmp(&b.0)));
    found.into_iter().map(|(swap, _)| swap).collect()
}

/// Maps every token that takes part in a replacement to one token that stands for its group.
struct Canon<'a> {
    stands_for: HashMap<&'a str, &'a str>,
}

impl<'a> Canon<'a> {
    fn new(swaps: &[(&'a str, &'a str)]) -> Self {
        fn root<'t>(map: &HashMap<&'t str, &'t str>, mut t: &'t str) -> &'t str {
            while let Some(next) = map.get(t) {
                t = *next;
            }
            t
        }
        let mut map = HashMap::new();
        for &(from, to) in swaps {
            let (a, b) = (root(&map, from), root(&map, to));
            if a != b {
                map.insert(a, b);
            }
        }
        let stands_for = map.keys().map(|&k| (k, root(&map, k))).collect();
        Canon { stands_for }
    }

    fn key<'k>(&self, body: &'k str) -> std::borrow::Cow<'k, str> {
        let parts = tokens(body);
        if !parts.iter().any(|t| self.stands_for.contains_key(*t)) {
            return body.into();
        }
        parts.into_iter().map(|t| self.stands_for.get(t).copied().unwrap_or(t)).collect::<String>().into()
    }
}

/// Claims the runs of rows that are in both files. A run is claimed from its first to its last
/// row that is the only one of its kind. Such a row can be in one run only, so no two claims
/// overlap. `grow` adds the common rows at the ends of a run afterwards.
fn claim_runs(o: &[Row], n: &[Row], ok: &[u32], nk: &[u32], map: &mut Map) -> Vec<Tile> {
    let mut places: HashMap<u32, Vec<usize>> = HashMap::new();
    for (j, k) in nk.iter().enumerate() {
        places.entry(*k).or_default().push(j);
    }
    let mut uses: HashMap<u32, usize> = HashMap::new();
    for k in ok {
        *uses.entry(*k).or_default() += 1;
    }
    let only = |k: u32| uses[&k] == 1 && places.get(&k).is_some_and(|p| p.len() == 1);

    let mut tiles = Vec::new();
    for i in 0..o.len() {
        if map.o2n[i].is_some() || !only(ok[i]) {
            continue;
        }
        let j = places[&ok[i]][0];
        let way = shift(o[i].text, n[j].text);
        let linked = |k: &usize| {
            let (a, b) = (i + k, j + k);
            a < o.len() && b < n.len() && ok[a] == nk[b] && shift(o[a].text, n[b].text) == way
        };
        let reach = (1..).take_while(linked).count();
        let len = 1 + (1..=reach).rev().find(|k| only(ok[i + k])).unwrap_or(0);
        (0..len).for_each(|k| map.pair(i + k, j + k));
        tiles.push(Tile { old: i, new: j, len });
    }
    tiles.sort_by_key(|t| t.new);
    tiles
}

/// Adds to each tile the rows at its ends that continue it. A common line, like `return x`,
/// can continue more than one tile. It goes to a tile it touches before one that a blank line
/// separates it from, and to a tile that stayed before one that moved.
fn grow(tiles: &mut [Tile], stay: &[bool], o: &[Row], n: &[Row], ok: &[u32], nk: &[u32], map: &mut Map) {
    let mut order: Vec<usize> = (0..tiles.len()).collect();
    order.sort_by_key(|&t| (!stay[t], std::cmp::Reverse(tiles[t].len)));
    // True if no blank line is between row k and the row before it.
    let touches = |rows: &[Row], k: usize| rows[k].no - rows[k - 1].no == 1;
    for touching in [true, false] {
        for &t in &order {
            let tile = &mut tiles[t];
            let way = shift(o[tile.old].text, n[tile.new].text);
            let fits = |i: usize, j: usize, map: &Map| {
                map.o2n[i].is_none() && map.n2o[j].is_none() && ok[i] == nk[j] && shift(o[i].text, n[j].text) == way
            };
            let near = |i: usize, j: usize| !touching || (touches(o, i) && touches(n, j));
            while tile.old > 0 && tile.new > 0 && fits(tile.old - 1, tile.new - 1, map) && near(tile.old, tile.new) {
                (tile.old, tile.new, tile.len) = (tile.old - 1, tile.new - 1, tile.len + 1);
                map.pair(tile.old, tile.new);
            }
            loop {
                let (i, j) = (tile.old + tile.len, tile.new + tile.len);
                if i == o.len() || j == n.len() || !fits(i, j, map) || !near(i, j) {
                    break;
                }
                map.pair(i, j);
                tile.len += 1;
            }
        }
    }
}

/// What it costs to call a run moved. Lines at the run's lowest indentation count most, so
/// one class that passed seven functions is the block that moved, whatever their sizes.
fn weight(t: &Tile, n: &[Row]) -> u64 {
    let rows = &n[t.new..t.new + t.len];
    let least = rows.iter().map(|r| r.width).min().unwrap_or(0);
    let heads = rows.iter().filter(|r| r.width == least).count();
    heads as u64 * HEAD_WEIGHT + t.len as u64
}

/// For tiles in the order of the new file: true for those in the heaviest set that is also in
/// the order of the old file.
fn heaviest_in_order(tiles: &[Tile], n: &[Row]) -> Vec<bool> {
    let count = tiles.len();
    let mut by_old: Vec<usize> = (0..count).collect();
    by_old.sort_by_key(|&t| tiles[t].old);
    let mut rank = vec![0; count];
    for (r, &t) in by_old.iter().enumerate() {
        rank[t] = r + 1;
    }
    // best[r] covers ranks up to r (a Fenwick tree): the heaviest chain and the tile it ends on.
    let mut best = vec![(0u64, 0usize); count + 1];
    let mut before = vec![0; count];
    let mut top = (0, 0);
    for (t, tile) in tiles.iter().enumerate() {
        let mut found = (0, 0);
        let mut r = rank[t] - 1;
        while r > 0 {
            if best[r].0 > found.0 {
                found = best[r];
            }
            r &= r - 1;
        }
        let total = found.0 + weight(tile, n);
        before[t] = found.1;
        let mut r = rank[t];
        while r <= count {
            if total > best[r].0 {
                best[r] = (total, t + 1);
            }
            r += r & r.wrapping_neg();
        }
        if total > top.0 {
            top = (total, t + 1);
        }
    }
    let mut stay = vec![false; count];
    let mut t = top.1;
    while t > 0 {
        stay[t - 1] = true;
        t = before[t - 1];
    }
    stay
}

/// Aligns the rows nobody claimed, between each two tiles that stayed.
fn align_rest(tiles: &[Tile], stay: &[bool], ok: &[u32], nk: &[u32], map: &mut Map, deadline: Instant) {
    let ends = Tile { old: ok.len(), new: nk.len(), len: 0 };
    let (mut old_from, mut new_from) = (0, 0);
    for t in tiles.iter().zip(stay).filter(|(_, s)| **s).map(|(t, _)| t).chain([&ends]) {
        let a: Vec<usize> = (old_from..t.old).filter(|&i| map.o2n[i].is_none()).collect();
        let b: Vec<usize> = (new_from..t.new).filter(|&j| map.n2o[j].is_none()).collect();
        if !a.is_empty() && !b.is_empty() {
            let (x, y): (Vec<u32>, Vec<u32>) = (a.iter().map(|&i| ok[i]).collect(), b.iter().map(|&j| nk[j]).collect());
            for (p, q) in align(&x, &y, deadline).0 {
                map.pair(a[p], b[q]);
            }
        }
        (old_from, new_from) = (t.old + t.len, t.new + t.len);
    }
}

/// Unpairs the rows that are islands in a rewritten stretch: a run of at most `ISLAND_MAX`
/// rows with larger changes before and after it, in both files. A lone `}` that a rewritten
/// function shares with the old one would otherwise split one changed block into many.
fn dissolve(map: &mut Map) {
    // The rows in one file only, straight before `at` and straight after `at + len`.
    let around = |pairs: &[Option<usize>], at: usize, len: usize| {
        let before = pairs[..at].iter().rev().take_while(|p| p.is_none()).count();
        let after = pairs[at + len..].iter().take_while(|p| p.is_none()).count();
        (before, after)
    };
    loop {
        let (mut islands, mut j) = (Vec::new(), 0);
        while j < map.n2o.len() {
            let Some(i) = map.n2o[j] else {
                j += 1;
                continue;
            };
            let len = 1 + (1..map.n2o.len() - j).take_while(|k| map.n2o[j + k] == Some(i + k)).count();
            let (old, new) = (around(&map.o2n, i, len), around(&map.n2o, j, len));
            let changed = [old.0, old.1, new.0, new.1].iter().all(|&rows| rows > 0);
            if len <= ISLAND_MAX && changed && old.0.max(new.0) >= 2 * len && old.1.max(new.1) >= 2 * len {
                islands.push((i, j, len));
            }
            j += len;
        }
        if islands.is_empty() {
            return;
        }
        for (i, j, len) in islands {
            map.o2n[i..i + len].fill(None);
            map.n2o[j..j + len].fill(None);
        }
    }
}

/// Lines of `rows` indented less than the first: how many, and the first of them.
fn outdented(rows: &[Row]) -> Option<(usize, usize)> {
    let mut less = rows.iter().filter(|r| r.width < rows[0].width);
    let first = less.next()?;
    Some((1 + less.count(), first.no))
}

fn replacements(o: &[Row], n: &[Row], map: &Map) -> Vec<Replacement> {
    let mut count: HashMap<(&str, &str), usize> = HashMap::new();
    for (j, i) in map.n2o.iter().enumerate() {
        let Some(i) = *i else { continue };
        let (x, y) = (tokens(o[i].body), tokens(n[j].body));
        if o[i].body == n[j].body || x.len() != y.len() {
            continue;
        }
        let mut swaps: Vec<_> = x.into_iter().zip(y).filter(|(p, q)| p != q).collect();
        swaps.sort();
        swaps.dedup();
        for s in swaps {
            *count.entry(s).or_default() += 1;
        }
    }
    let mut found: Vec<_> = count.into_iter().collect();
    found.sort_by(|a, b| b.1.cmp(&a.1).then(a.0.cmp(&b.0)));
    found.truncate(MAX_REPLACEMENTS);
    let replacement = |((from, to), lines): ((&str, &str), usize)| Replacement {
        from: from.to_string(),
        to: to.to_string(),
        lines,
        left: n.iter().filter(|r| tokens(r.body).contains(&from)).count(),
    };
    found.into_iter().map(replacement).collect()
}

/// Splits a run of deleted rows where a new unit starts: after a blank line,
/// at a row indented no more than the first row of the piece so far. That piece must have a
/// body, rows indented more than its first one, so paragraphs of prose stay together.
fn pieces(rows: &[Row], run: Range<usize>) -> Vec<Range<usize>> {
    let mut out: Vec<Range<usize>> = Vec::new();
    let mut body = false;
    for k in run {
        let starts = |first: &Row| body && rows[k].no - rows[k - 1].no > 1 && rows[k].width <= first.width;
        match out.last_mut() {
            Some(piece) if !starts(&rows[piece.start]) => {
                body |= rows[k].width > rows[piece.start].width;
                piece.end = k + 1;
            }
            _ => {
                out.push(k..k + 1);
                body = false;
            }
        }
    }
    out
}

/// What the block that moved from old row `i` to new row `j` passed on its way: the rows that
/// stayed and are on its other side now.
fn passed(n: &[Row], map: &Map, i: usize, j: usize, len: usize) -> Option<(bool, Span)> {
    let stayed = |r: &usize| map.stays[*r] && map.n2o[*r].is_some();
    let mut above = (0..j).rev().filter(stayed).take_while(|r| map.n2o[*r] > Some(i));
    if let Some(last) = above.next() {
        return Some((true, (n[above.last().unwrap_or(last)].no, n[last].no)));
    }
    let mut below = (j + len..n.len()).filter(stayed).take_while(|r| map.n2o[*r] < Some(i));
    let first = below.next()?;
    Some((false, (n[first].no, n[below.last().unwrap_or(first)].no)))
}

/// A block of rows of the old file, of the new file, or of both.
fn block<'a>(kind: Kind, old: Option<&[Row]>, new: Option<&[Row]>, after: usize) -> Block<'a> {
    let span = |rows: &[Row]| (rows[0].no, rows[rows.len() - 1].no);
    // Text that is new has no block it could have run past.
    let whole = match kind {
        Kind::Inserted | Kind::Changed => None,
        _ => new.or(old),
    };
    let (old, new, outdented) = (old.map(span), new.map(span), whole.and_then(outdented));
    Block { kind, old, new, indent: Indent::Same, after, outdented, passed: None }
}

/// The blocks, in the order of the new file.
fn blocks<'a>(o: &[Row<'a>], n: &[Row<'a>], map: &Map) -> Vec<Block<'a>> {
    let above = |j: usize| if j == 0 { 0 } else { n[j - 1].no };
    let mut found = Vec::new();

    // Rows that are in both files: moved, or re-indented where they were.
    let mut j = 0;
    while j < n.len() {
        let Some(i) = map.n2o[j] else {
            j += 1;
            continue;
        };
        let (stays, indent) = (map.stays[j], shift(o[i].text, n[j].text));
        let same = |k: &usize| {
            let (a, b) = (i + k, j + k);
            b < n.len() && map.n2o[b] == Some(a) && map.stays[b] == stays && shift(o[a].text, n[b].text) == indent
        };
        let len = 1 + (1..).take_while(same).count();
        let (old, new) = (Some(&o[i..i + len]), Some(&n[j..j + len]));
        if !stays {
            found.push(Block { indent, passed: passed(n, map, i, j, len), ..block(Kind::Moved, old, new, above(j)) });
        } else if indent != Indent::Same {
            found.push(Block { indent, ..block(Kind::Reindented, old, new, above(j)) });
        }
        j += len;
    }

    // Rows that are in one file only. An inserted run that took the place of a deleted one
    // makes a changed block.
    let runs = |taken: &[Option<usize>]| {
        let mut runs: Vec<Range<usize>> = Vec::new();
        for k in (0..taken.len()).filter(|&k| taken[k].is_none()) {
            match runs.last_mut() {
                Some(r) if r.end == k => r.end += 1,
                _ => runs.push(k..k + 1),
            }
        }
        runs
    };
    let mut inserted: Vec<Option<Range<usize>>> = runs(&map.n2o).into_iter().map(Some).collect();
    for gone in runs(&map.o2n) {
        // The rows of the new file that the rows around the run are now, if they stayed. A
        // neighbor that moved away says nothing about the place the run left.
        let paired = |i: usize| map.o2n[i].expect("the rows around a run are paired");
        let prev = gone.start.checked_sub(1).map(paired);
        let next = (gone.end < o.len()).then(|| paired(gone.end));
        let stayed = |j: &usize| map.stays[*j];
        let replaces = |run: &Range<usize>| {
            let follows = prev.filter(stayed).map_or(gone.start == 0 && run.start == 0, |j| j + 1 == run.start);
            let precedes = next.filter(stayed).map_or(gone.end == o.len() && run.end == n.len(), |j| j == run.end);
            follows || precedes
        };
        let came = inserted.iter_mut().find(|run| run.as_ref().is_some_and(&replaces)).and_then(Option::take);
        if let Some(run) = came {
            found.push(block(Kind::Changed, Some(&o[gone]), Some(&n[run.clone()]), above(run.start)));
            continue;
        }
        // The place the run left, in the new file: next to a neighbor that stayed, if one did.
        let after = match (prev.filter(stayed), next.filter(stayed), prev, next) {
            (Some(j), ..) | (None, None, Some(j), _) => n[j].no,
            (None, Some(j), ..) | (None, None, None, Some(j)) => above(j),
            (None, None, None, None) => 0,
        };
        found.extend(pieces(o, gone).into_iter().map(|piece| block(Kind::Deleted, Some(&o[piece]), None, after)));
    }
    for run in inserted.into_iter().flatten() {
        found.push(block(Kind::Inserted, None, Some(&n[run.clone()]), above(run.start)));
    }
    found.sort_by_key(|b| match b.new {
        Some((first, _)) => (first, 0),
        None => (b.after, 1),
    });
    found
}

/// The runs of blank lines whose length changed.
fn spacing(o: &[Row], n: &[Row], map: &Map) -> Vec<Spacing> {
    // The blank lines above old row i. None above the first row and below the last one: the
    // ends of the file say nothing about the space between two blocks.
    let gap = |i: usize| (i > 0 && i < o.len()).then(|| o[i].no - o[i - 1].no - 1);
    let mut found = Vec::new();
    if let (Some(a), Some(b)) = (o.first(), n.first()) {
        if a.no != b.no {
            found.push(Spacing { warn: true, above: 0, now: b.no - 1, was: vec![a.no - 1] });
        }
    }
    for j in 1..n.len() {
        let (above, now) = (n[j - 1].no, n[j].no - n[j - 1].no - 1);
        // Next to new text, only a run that was there and still is there is compared.
        let kept = |was: &usize| *was > 0 && now > 0;
        let (warn, mut was): (bool, Vec<usize>) = match (map.n2o[j - 1], map.n2o[j]) {
            // The same two lines as before, with other space between them.
            (Some(p), Some(q)) if q == p + 1 => (false, gap(q).into_iter().collect()),
            // Two blocks joined here. The space should be what one of them had.
            (Some(p), Some(q)) => (true, gap(p + 1).into_iter().chain(gap(q)).collect()),
            (Some(p), None) => (true, gap(p + 1).into_iter().filter(kept).collect()),
            (None, Some(q)) => (true, gap(q).into_iter().filter(kept).collect()),
            (None, None) => continue,
        };
        was.dedup();
        if !was.is_empty() && !was.contains(&now) {
            found.push(Spacing { warn, above, now, was });
        }
    }
    found
}

pub fn analyze<'a>(old_text: &'a str, new_text: &'a str) -> Analysis<'a> {
    let (old, new) = (split_lines(old_text), split_lines(new_text));
    let (o, n) = (rows(&old), rows(&new));
    let deadline = Instant::now() + DIFF_TIME;

    let canon = Canon::new(&discover(&o, &n, deadline));
    let mut ids = HashMap::new();
    let ok: Vec<u32> = o.iter().map(|r| intern(&mut ids, canon.key(r.body))).collect();
    let nk: Vec<u32> = n.iter().map(|r| intern(&mut ids, canon.key(r.body))).collect();

    let mut map = Map { o2n: vec![None; o.len()], n2o: vec![None; n.len()], stays: vec![true; n.len()] };
    let mut tiles = claim_runs(&o, &n, &ok, &nk, &mut map);
    let stay = heaviest_in_order(&tiles, &n);
    grow(&mut tiles, &stay, &o, &n, &ok, &nk, &mut map);
    for (t, _) in tiles.iter().zip(&stay).filter(|(_, stay)| !**stay) {
        map.stays[t.new..t.new + t.len].fill(false);
    }
    align_rest(&tiles, &stay, &ok, &nk, &mut map, deadline);
    dissolve(&mut map);

    Analysis {
        replacements: replacements(&o, &n, &map),
        blocks: blocks(&o, &n, &map),
        spacing: spacing(&o, &n, &map),
        end_newlines: (trailing_newlines(old_text), trailing_newlines(new_text)),
        old,
        new,
    }
}

fn cut(s: &str, max: usize) -> String {
    if s.chars().count() <= max {
        return s.to_string();
    }
    s.chars().take(max - 3).chain("...".chars()).collect()
}

fn count(n: usize, word: &str) -> String {
    format!("{n} {word}{}", if n == 1 { "" } else { "s" })
}

fn lines((first, last): Span) -> String {
    count(last - first + 1, "line")
}

fn range((first, last): Span) -> String {
    if first == last {
        first.to_string()
    } else {
        format!("{first}-{last}")
    }
}

fn indent_text(indent: Indent) -> String {
    let unit = |s: &str| {
        let n = s.chars().count();
        if s.chars().all(|c| c == ' ') {
            count(n, "space")
        } else if s.chars().all(|c| c == '\t') {
            count(n, "tab")
        } else {
            format!("{n} of whitespace")
        }
    };
    match indent {
        Indent::Same => String::new(),
        Indent::Add(s) => format!(", indent +{}", unit(s)),
        Indent::Remove(s) => format!(", indent -{}", unit(s)),
        Indent::Other => ", indent changed".to_string(),
    }
}

/// The first line of a block, to name it.
fn label(text: &[&str], span: Span) -> String {
    format!("  ({})", cut(text[span.0 - 1].trim(), 60))
}

/// What happened to a block, in one line. Two more lines follow where they apply: what a moved
/// block passed, and a warning if the block holds lines indented less than its first one.
fn headlines(a: &Analysis, b: &Block) -> Vec<String> {
    let first = match (b.kind, b.old, b.new) {
        (Kind::Deleted, Some(o), _) => format!("deleted {}: {}{}", lines(o), range(o), label(&a.old, o)),
        (Kind::Inserted, _, Some(n)) => format!("inserted {}: {}{}", lines(n), range(n), label(&a.new, n)),
        (Kind::Changed, Some(o), Some(n)) => {
            let to = if o.1 - o.0 == n.1 - n.0 { String::new() } else { format!(" to {}", n.1 - n.0 + 1) };
            format!("changed {}{to}: {} -> {}{}", lines(o), range(o), range(n), label(&a.new, n))
        }
        (_, Some(o), Some(n)) => {
            let verb = if b.kind == Kind::Moved { "moved" } else { "reindented" };
            format!("{verb} {}: {} -> {}{}{}", lines(n), range(o), range(n), indent_text(b.indent), label(&a.new, n))
        }
        _ => String::new(),
    };
    let passed = b.passed.map(|(down, span)| {
        let way = if down { "down" } else { "up" };
        format!("  {way} past {}: {}{}", lines(span), range(span), label(&a.new, span))
    });
    let outdented = b.outdented.map(|(n, first)| {
        let (verb, sign) = (if n == 1 { "line is" } else { "lines are" }, if b.new.is_some() { '+' } else { '-' });
        format!("  WARNING: {n} {verb} indented less than the block's first line, from {sign}{first}")
    });
    [Some(first), passed, outdented].into_iter().flatten().collect()
}

fn spacing_text(s: &Spacing) -> String {
    let now = match s.now {
        0 if s.above == 0 => "no blank line at the start of the file".to_string(),
        0 => format!("no blank line between {} and {}", s.above, s.above + 1),
        n => format!("{} at {}", count(n, "blank line"), range((s.above + 1, s.above + n))),
    };
    let was = match s.was[..] {
        [one] => one.to_string(),
        _ => format!("{} below the line above, {} above the line below", s.was[0], s.was[1]),
    };
    format!("{}{now}, was {was}", if s.warn { "WARNING: " } else { "spacing: " })
}

/// How much of each block to show. The first level that fits in the summary is used.
struct Level {
    /// Lines at each end of the block.
    edge: usize,
    /// The lines of the new file before and after the block.
    context: bool,
    /// Lines inside the block that show what it holds.
    marks: usize,
}

const LEVELS: [Level; 5] = [
    Level { edge: 2, context: true, marks: 3 },
    Level { edge: 2, context: true, marks: 1 },
    Level { edge: 1, context: true, marks: 1 },
    Level { edge: 1, context: false, marks: 0 },
    Level { edge: 0, context: false, marks: 0 },
];

/// The lines of a block to show: its first and last lines, and a few lines that show what
/// the block holds. Those are the lines indented less than the first line or, with `peers`,
/// as much as the first line.
fn shown(text: &[&str], (first, last): Span, level: &Level, peers: bool) -> Vec<usize> {
    if last - first < 2 * level.edge + 1 {
        return (first..=last).collect();
    }
    let mut picks: Vec<usize> = (first..first + level.edge).chain(last + 1 - level.edge..=last).collect();
    let inner = || (first + 1..=last).filter(|&no| !text[no - 1].trim().is_empty());
    let least = inner().map(|no| width(text[no - 1])).min().unwrap_or(0).min(width(text[first - 1]));
    if least < width(text[first - 1]) || peers {
        let marks = inner().filter(|&no| width(text[no - 1]) == least && !picks.contains(&no)).take(level.marks);
        picks.extend(marks.collect::<Vec<_>>());
    }
    picks.sort();
    picks
}

fn excerpt(a: &Analysis, b: &Block, level: &Level) -> Vec<String> {
    let mut out = Vec::new();
    if level.edge == 0 {
        return out;
    }
    let mut side = |text: &[&str], sign: char, span: Span, peers: bool| {
        let mut last = span.0;
        for no in shown(text, span, level, peers) {
            if no > last + 1 {
                out.push("   ...".to_string());
            }
            out.push(cut(&format!("  {sign}{no}:{}", text[no - 1]), MAX_WIDTH));
            last = no;
        }
    };
    if let (Some(span), true) = (b.old, b.new.is_none() || b.kind == Kind::Changed) {
        side(&a.old, '-', span, b.kind == Kind::Deleted);
    }
    if let Some(span) = b.new {
        side(&a.new, '+', span, b.kind == Kind::Moved);
    }
    if level.context {
        let line = |no: usize| cut(&format!("   {no}:{}", a.new[no - 1]), MAX_WIDTH);
        if b.after > 0 {
            out.insert(0, line(b.after));
        }
        let end = b.new.map_or(b.after, |n| n.1);
        let next = (end + 1..=a.new.len()).find(|&no| !a.new[no - 1].trim().is_empty());
        out.push(next.map_or("   (end of file)".to_string(), line));
    }
    out
}

pub fn render(a: &Analysis, name: &str, totals: &Totals) -> String {
    let Totals { added, removed, hunks } = totals;
    let (hunks, before, after) = (count(*hunks, "hunk"), a.old.len(), a.new.len());
    let mut top = vec![format!("{name}: +{added} -{removed} lines in {hunks}; {before} -> {after} lines"), LEGEND.to_string()];
    let (before, after) = a.end_newlines;
    if before != after {
        let blank = match after {
            0 | 1 => String::new(),
            n => format!(" ({} at the end)", count(n - 1, "blank line")),
        };
        top.push(format!("WARNING: file ends with {}, was {before}{blank}", count(after, "newline")));
    }
    for (warn, most) in [(true, MAX_WARNINGS), (false, MAX_NOTES)] {
        let all: Vec<_> = a.spacing.iter().filter(|s| s.warn == warn).collect();
        top.extend(all.iter().take(most).map(|s| spacing_text(s)));
        if all.len() > most {
            let more = count(all.len() - most, "more run");
            top.push(format!("{}{more} of blank lines changed", if warn { "WARNING: " } else { "spacing: " }));
        }
    }
    for r in &a.replacements {
        let left = match r.left {
            0 => "none left".to_string(),
            n => format!("{} still {} {}", count(n, "line"), if n == 1 { "has" } else { "have" }, r.from),
        };
        top.push(format!("replaced on {}: {} -> {} ({left})", count(r.lines, "line"), r.from, r.to));
    }

    let room = OUTPUT_MAX_LINES.saturating_sub(top.len());
    let build = |level: &Level| -> Vec<Vec<String>> {
        a.blocks.iter().map(|b| headlines(a, b).into_iter().chain(excerpt(a, b, level)).collect()).collect()
    };
    let fits = |body: &Vec<Vec<String>>| body.iter().map(Vec::len).sum::<usize>() <= room;
    let body = LEVELS.iter().map(build).find(fits).unwrap_or_else(|| build(&LEVELS[LEVELS.len() - 1]));

    let mut out = top;
    let mut left = a.blocks.len();
    for lines in body {
        // Keep one line for the note that says how many blocks are not shown.
        if out.len() + lines.len() + usize::from(left > 1) > OUTPUT_MAX_LINES {
            break;
        }
        out.extend(lines);
        left -= 1;
    }
    if left > 0 {
        out.push(format!("... {} not shown; --diff full shows every change", count(left, "block")));
    }
    out.iter().map(|l| format!("{l}\n")).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    /// `count` lines that are all different: "alpha 1", "alpha 2", ...
    fn part(word: &str, count: usize) -> String {
        (1..=count).map(|i| format!("{word} {i}\n")).collect()
    }

    /// A block shaped like a class: a first line and an indented body.
    fn class(name: &str, lines: usize) -> String {
        let body: String = (1..lines).map(|i| format!("    {name}_{i} = {i}\n")).collect();
        format!("class {name}:\n{body}")
    }

    fn kinds(a: &Analysis) -> Vec<(Kind, Option<Span>, Option<Span>)> {
        a.blocks.iter().map(|b| (b.kind, b.old, b.new)).collect()
    }

    /// The analysis borrows the two texts. A test leaks them, to keep what it builds in place.
    fn compare(old: impl Into<String>, new: impl Into<String>) -> Analysis<'static> {
        analyze(old.into().leak(), new.into().leak())
    }

    fn totals() -> Totals {
        Totals { added: 0, removed: 0, hunks: 0 }
    }

    #[test]
    fn counts_the_newlines_at_the_end() {
        assert_eq!(trailing_newlines("a"), 0);
        assert_eq!(trailing_newlines("a\n"), 1);
        assert_eq!(trailing_newlines("a\n\n\n"), 3);
        assert_eq!(trailing_newlines("a\r\n\r\n"), 2);
        assert_eq!(trailing_newlines(""), 0);
    }

    #[test]
    fn splits_a_line_into_tokens() {
        assert_eq!(tokens("self.log_event(\"x\",  1)"), ["self", ".", "log_event", "(", "\"", "x", "\"", ",", "  ", "1", ")"]);
        assert_eq!(token_swap("a.log(x) + log(y)", "a.emit(x) + emit(y)"), Some(("log", "emit")));
        assert_eq!(token_swap("a.log(x)", "b.emit(x)"), None);
        assert_eq!(token_swap("a.log(x)", "a.log(x, y)"), None);
    }

    #[test]
    fn finds_a_moved_block() {
        let (a, b, c) = (part("alpha", 10), part("beta", 4), part("gamma", 10));
        let found = compare(format!("{a}{b}{c}"), format!("{a}{c}{b}"));
        assert_eq!(kinds(&found), [(Kind::Moved, Some((11, 14)), Some((21, 24)))]);
        assert_eq!(found.blocks[0].after, 20);
        assert!(found.spacing.is_empty() && found.replacements.is_empty());
    }

    #[test]
    fn the_block_that_moved_is_the_one_with_fewest_heads() {
        // One large class went below three small functions. The class moved, not the functions.
        let big = class("Big", 40);
        let small: String = ["f", "g", "h"].iter().map(|f| format!("def {f}():\n    return {f}\n")).collect();
        let found = compare(format!("top = 1\n{big}{small}"), format!("top = 1\n{small}{big}"));
        assert_eq!(kinds(&found), [(Kind::Moved, Some((2, 41)), Some((8, 47)))]);
    }

    #[test]
    fn a_renamed_line_does_not_split_a_moved_block() {
        let call = |name: &str, i: usize| format!("    self.{name}(\"step\", {i})\n");
        let text = |name: &str, first: &str, second: &str| {
            let calls: String = (0..6).map(|i| format!("value_{i} = {i}\n{}", call(name, i))).collect();
            format!("{calls}{first}{second}")
        };
        let block = |name: &str| format!("class Report:\n    total = 0\n{}    done = True\n", call(name, 99));
        let tail = part("tail", 8);
        let found = compare(text("log_event", &block("log_event"), &tail), text("emit_event", &tail, &block("emit_event")));
        assert_eq!(kinds(&found), [(Kind::Moved, Some((13, 16)), Some((21, 24)))]);
        let expected = Replacement { from: "log_event".into(), to: "emit_event".into(), lines: 7, left: 0 };
        assert_eq!(found.replacements, [expected]);
    }

    #[test]
    fn a_replacement_reports_the_lines_it_missed() {
        let old: String = (0..8).map(|i| format!("x{i} = load({i})\n")).collect();
        let new: String = (0..8).map(|i| format!("x{i} = {}({i})\n", if i < 6 { "read" } else { "load" })).collect();
        let found = compare(&old, &new);
        assert_eq!(found.replacements, [Replacement { from: "load".into(), to: "read".into(), lines: 6, left: 2 }]);
        assert!(found.blocks.is_empty());
    }

    #[test]
    fn finds_a_reindented_block() {
        let body: String = (0..9).map(|i| format!("        step_{i}()\n{}", if i % 3 == 2 { "\n" } else { "" })).collect();
        let old = format!("class A:\n    def f(self):\n{body}    def g(self):\n        pass\n");
        let new = old.replacen("        step_0", "        with lock:\n        step_0", 1);
        let new = (0..9).fold(new, |text, i| text.replace(&format!("    step_{i}()"), &format!("        step_{i}()")));
        let found = compare(&old, &new);
        let expected = [(Kind::Inserted, None, Some((3, 3))), (Kind::Reindented, Some((3, 13)), Some((4, 14)))];
        assert_eq!(kinds(&found), expected);
        assert_eq!(found.blocks[1].indent, Indent::Add("    "));
        assert_eq!(found.blocks[1].outdented, None);
        assert!(found.spacing.is_empty());
    }

    #[test]
    fn a_reindent_that_runs_past_its_block_is_flagged() {
        let old = "class A:\n    def f(self):\n        one()\n        two()\n\n    def g(self):\n        three()\n\n    def h(self):\n        four()\n";
        let new = "class A:\n    def f(self):\n            one()\n            two()\n\n        def g(self):\n            three()\n\n        def h(self):\n            four()\n";
        let found = compare(old, new);
        assert_eq!(kinds(&found), [(Kind::Reindented, Some((3, 10)), Some((3, 10)))]);
        assert_eq!(found.blocks[0].outdented, Some((2, 6)));
        let text = render(&found, "f.py", &totals());
        assert!(text.contains("WARNING: 2 lines are indented less than the block's first line, from +6"), "{text}");
        assert!(text.contains("  +6:        def g(self):"), "{text}");
    }

    #[test]
    fn a_rewritten_stretch_is_one_changed_block() {
        // The old and the new function share only a closing brace.
        let (top, end) = (part("top", 5), part("end", 5));
        let calls = |from: usize, to: usize| (from..to).map(|i| format!("    call({i});\n")).collect::<String>();
        let sums = |from: usize, to: usize| (from..to).map(|i| format!("    let v{i} = {i} + 1;\n")).collect::<String>();
        let old = format!("{top}{}}}\n{}{end}", calls(0, 4), calls(4, 8));
        let new = format!("{top}{}}}\n{}{end}", sums(0, 3), sums(3, 8));
        assert_eq!(kinds(&compare(&old, &new)), [(Kind::Changed, Some((6, 14)), Some((6, 14)))]);

        // Two lines changed and the line between them did not: that line is not an island.
        let found = compare("a = 1\nb = 2\nc = 3\nd = 4\ne = 5\n", "a = 1\nb = 20\nc = 3\nd = 40\ne = 5\n");
        assert_eq!(kinds(&found), [(Kind::Changed, Some((2, 2)), Some((2, 2))), (Kind::Changed, Some((4, 4)), Some((4, 4)))]);
    }

    #[test]
    fn finds_deleted_inserted_and_changed_blocks() {
        let (a, b, c) = (part("alpha", 6), part("beta", 3), part("gamma", 6));
        let found = compare(format!("{a}{b}{c}"), format!("{a}{c}"));
        assert_eq!(kinds(&found), [(Kind::Deleted, Some((7, 9)), None)]);
        assert_eq!(found.blocks[0].after, 6);

        let found = compare(format!("{a}{c}"), format!("{a}{b}{c}"));
        assert_eq!(kinds(&found), [(Kind::Inserted, None, Some((7, 9)))]);

        let found = compare(format!("{a}{b}{c}"), format!("{a}new 1\nnew 2\n{c}"));
        assert_eq!(kinds(&found), [(Kind::Changed, Some((7, 9)), Some((7, 8)))]);
    }

    #[test]
    fn a_block_is_reported_without_the_blank_lines_around_it() {
        let (a, b, c) = (class("A", 5), class("B", 5), class("C", 5));
        let old = format!("{a}\n\n{b}\n\n{c}");
        // The same deletion, taking the blank lines above the block or the ones below it.
        let found = compare(&old, format!("{a}\n\n{c}"));
        assert_eq!(kinds(&found), [(Kind::Deleted, Some((8, 12)), None)]);
        assert!(found.spacing.is_empty());
    }

    #[test]
    fn warns_about_blank_lines_left_by_a_deleted_block() {
        let (a, b, c) = (class("A", 5), class("B", 5), class("C", 5));
        let old = format!("{a}\n\n{b}\n\n{c}");
        let found = compare(&old, format!("{a}\n\n\n\n{c}"));
        assert_eq!(found.spacing, [Spacing { warn: true, above: 5, now: 4, was: vec![2] }]);
        let found = compare(&old, format!("{a}{c}"));
        assert_eq!(found.spacing, [Spacing { warn: true, above: 5, now: 0, was: vec![2] }]);
        let text = render(&found, "f.py", &totals());
        assert!(text.contains("WARNING: no blank line between 5 and 6, was 2"), "{text}");
    }

    #[test]
    fn warns_about_a_block_moved_to_the_end_with_the_blank_lines_below_it() {
        let (a, b, c) = (class("A", 5), class("B", 5), class("C", 9));
        let old = format!("{a}\n\n{b}\n\n{c}");
        let found = compare(&old, format!("{a}\n\n{c}{b}\n\n"));
        assert_eq!(kinds(&found), [(Kind::Moved, Some((8, 12)), Some((17, 21)))]);
        assert_eq!(found.spacing, [Spacing { warn: true, above: 16, now: 0, was: vec![2] }]);
        assert_eq!(found.end_newlines, (1, 3));
        let text = render(&found, "f.py", &totals());
        assert!(text.contains("WARNING: file ends with 3 newlines, was 1 (2 blank lines at the end)"), "{text}");

        // Moved with the blank lines above it: nothing to warn about.
        let found = compare(&old, format!("{a}\n\n{c}\n\n{b}"));
        assert_eq!(kinds(&found), [(Kind::Moved, Some((8, 12)), Some((19, 23)))]);
        assert!(found.spacing.is_empty());
        assert_eq!(found.end_newlines, (1, 1));
    }

    #[test]
    fn blank_lines_changed_between_the_same_two_lines_are_a_note() {
        let found = compare("a = 1\n\nb = 2\nc = 3\n", "a = 1\n\n\nb = 2\nc = 3\n");
        assert_eq!(found.spacing, [Spacing { warn: false, above: 1, now: 2, was: vec![1] }]);
        assert!(render(&found, "f.py", &totals()).contains("spacing: 2 blank lines at 2-3, was 1"));
    }

    #[test]
    fn new_text_with_no_blank_line_below_it_is_not_a_warning() {
        let found = compare("a = 1\n\nb = 2\n", "a = 1\n\nnew = 0\nb = 2\n");
        assert_eq!(kinds(&found), [(Kind::Inserted, None, Some((3, 3)))]);
        assert!(found.spacing.is_empty());
    }

    #[test]
    fn the_summary_is_bounded() {
        let old = part("line", 900);
        let new: String = (1..=900).map(|i| if i % 3 == 0 { format!("other {i} {}\n", i * 7) } else { format!("line {i}\n") }).collect();
        let found = compare(&old, &new);
        assert_eq!(found.blocks.len(), 300);
        let text = render(&found, "f.py", &totals());
        assert_eq!(text.lines().count(), OUTPUT_MAX_LINES, "{text}");
        assert!(text.ends_with("blocks not shown; --diff full shows every change\n"), "{text}");
    }

    #[test]
    fn a_long_block_shows_its_first_and_last_lines() {
        let (a, b) = (part("alpha", 5), class("Gone", 30));
        let found = compare(format!("{a}{b}{a}"), format!("{a}{a}"));
        let text = render(&found, "f.py", &totals());
        let expected = "deleted 30 lines: 6-35  (class Gone:)\n   5:alpha 5\n  -6:class Gone:\n  -7:    Gone_1 = 1\n   ...\n  -34:    Gone_28 = 28\n  -35:    Gone_29 = 29\n   6:alpha 1\n";
        assert!(text.ends_with(expected), "{text}");
    }
}
