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
//! 3. Lines are compared without the whitespace around them. A line that is the only one of
//!    its kind in both files anchors a run of lines that the files share. One run has one
//!    change of indentation.
//! 4. Of those runs, the heaviest set that kept its order stayed. The others moved.
//! 5. Common lines at the ends of a run join the run they touch. The lines still unclaimed are
//!    aligned with an ordinary diff, between the runs that stayed. A line or two that a rewritten
//!    stretch shares with the text it replaced is not reported on its own.
//!
//! Only step 5 can take long, and it has a deadline. Every other step takes time in proportion
//! to the size of the files, or to that times its logarithm.

use similar::{capture_diff_slices_deadline, Algorithm, DiffOp};
use std::collections::hash_map::DefaultHasher;
use std::collections::HashMap;
use std::hash::{Hash, Hasher};
use std::ops::Range;
use std::time::{Duration, Instant};

/// In `auto` mode the diff is printed in full if it changes at most this many lines...
pub const FULL_DIFF_MAX_CHANGED: usize = 60;
/// ...and is no longer than this with the usual context. The summary is never longer either.
pub const OUTPUT_MAX_LINES: usize = 80;
/// The time for a diff that may be replaced by the summary, and the time for the analysis.
pub const DIFF_TIME: Duration = Duration::from_secs(2);
pub const ANALYSIS_TIME: Duration = Duration::from_secs(2);

/// A token replacement counts as equal in the whole file once it explains this many lines.
const REPEAT_MIN: usize = 3;
/// A line with more tokens than this is not searched for a replacement.
const SWAP_MAX_TOKENS: usize = 64;
/// Moving one more block costs more than moving any number of lines: see `weight`.
const HEAD_WEIGHT: u64 = 1 << 24;
/// A run of at most this many rows between two larger changes counts as part of the change.
const ISLAND_MAX: usize = 2;
const DISSOLVE_PASSES: usize = 8;
/// A deleted run is reported unit by unit if it holds at most this many units.
const SPLIT_MAX: usize = 3;
const MAX_REPLACEMENTS: usize = 5;
const MAX_WARNINGS: usize = 8;
const MAX_NOTES: usize = 4;
/// Excerpt lines are cut at this many characters.
const MAX_WIDTH: usize = 100;

const LEGEND: &str =
    "summary (--diff full prints the diff). -N: old line. +N: new line. N: line next to the block, in the new file.";

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
    /// Lines indented less than the block's first line: how many, and the first of them. Set
    /// only if the block started inside another block that did not go with it. See `ran_past`.
    pub outdented: Option<(usize, usize)>,
    /// For a block that moved: true if it went down, and the lines it passed, which stayed.
    pub passed: Option<(bool, Span)>,
    /// For a moved or deleted block: the lines where its other units start. Such a line is
    /// indented as much as the first line and follows a blank line. A block of three functions
    /// has two. A block without a body, lines indented more than its first, has none.
    pub heads: Vec<usize>,
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
    /// True where two blocks were joined and the run fits neither of them.
    pub warn: bool,
    /// The line of the new file above the run. 0 is the start of the file.
    pub above: usize,
    pub now: usize,
    /// The blank lines that followed the line above and preceded the line below, before.
    pub was: Vec<usize>,
}

/// Lines that are the same but for their whitespace or their ending.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Unseen {
    /// Lines whose ending changed: from, to, how many. An ending is "LF" or "CRLF".
    pub endings: Vec<(&'static str, &'static str, usize)>,
    /// Blank lines with other whitespace in them: how many, and the first in the new file.
    pub blank: Option<(usize, usize)>,
    /// Other lines with other whitespace at their end: how many, and the first in the new file.
    pub trailing: Option<(usize, usize)>,
}

pub struct Analysis<'a> {
    pub old: Vec<&'a str>,
    pub new: Vec<&'a str>,
    pub replacements: Vec<Replacement>,
    pub blocks: Vec<Block<'a>>,
    pub spacing: Vec<Spacing>,
    pub unseen: Unseen,
    /// Newlines at the end of the file, before and after.
    pub end_newlines: (usize, usize),
    /// True if the time ran out. There are then no blocks, or coarser ones than there could be.
    pub timed_out: bool,
}

/// The counts of the full diff, for the first line of the summary.
pub struct Totals {
    pub added: usize,
    pub removed: usize,
    pub hunks: usize,
    /// True if the diff ran out of time, so that it may count more lines than have changed.
    pub rough: bool,
}

/// A line that is not blank.
#[derive(Clone, Copy)]
struct Row<'a> {
    no: usize,
    text: &'a str,
    /// The text without the whitespace around it.
    core: &'a str,
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

/// The lines of a text, and what ends each of them: "\n", "\r\n", or nothing after the last
/// line. `str::lines` finds the same lines and drops the endings, which are needed here.
fn split(s: &str) -> (Vec<&str>, Vec<&str>) {
    let line = |raw: &str| raw.strip_suffix('\n').map_or(raw.len(), |text| text.strip_suffix('\r').unwrap_or(text).len());
    s.split_inclusive('\n').map(|raw| raw.split_at(line(raw))).unzip()
}

/// The newlines at the end of a file: the one that ends its last line that is not blank, and
/// one for each blank line below it. A line with nothing but whitespace is a blank line.
fn newlines_at_end(lines: &[&str], ends: &[&str]) -> usize {
    let blank = lines.iter().rev().take_while(|l| l.trim().is_empty()).count();
    let last = (lines.len() - blank).saturating_sub(1);
    ends[last..].iter().filter(|end| !end.is_empty()).count()
}

fn lead(s: &str) -> &str {
    &s[..s.len() - s.trim_start().len()]
}

fn width(s: &str) -> usize {
    lead(s).chars().map(|c| if c == '\t' { 8 } else { 1 }).sum()
}

fn rows<'a>(lines: &[&'a str]) -> Vec<Row<'a>> {
    let row = |(i, l): (usize, &&'a str)| Row { no: i + 1, text: l, core: l.trim(), width: width(l) };
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

fn intern<'k>(ids: &mut HashMap<std::borrow::Cow<'k, str>, u32>, key: std::borrow::Cow<'k, str>) -> u32 {
    let next = ids.len() as u32;
    *ids.entry(key).or_insert(next)
}

/// An ordinary diff: the pairs of equal items.
fn align(a: &[u32], b: &[u32], deadline: Instant) -> Vec<(usize, usize)> {
    if a.len() == 1 && b.len() == 1 {
        return if a == b { vec![(0, 0)] } else { Vec::new() };
    }
    let mut pairs = Vec::new();
    for op in capture_diff_slices_deadline(Algorithm::Myers, a, b, Some(deadline)) {
        if let DiffOp::Equal { old_index, new_index, len } = op {
            pairs.extend((0..len).map(|k| (old_index + k, new_index + k)));
        }
    }
    pairs
}

/// Token replacements that explain at least `REPEAT_MIN` changed lines, most frequent first.
///
/// A line that is in one file more often than in the other went or came. Each such line is
/// hashed once for each of its tokens, with that token left out. A line that went and a line
/// that came with the same hash differ in that one token, wherever in the files they are. No
/// diff is needed for this, so it takes the same time however much of the file has changed.
fn discover<'a>(o: &[Row<'a>], n: &[Row<'a>]) -> Vec<(&'a str, &'a str)> {
    let mut surplus: HashMap<&'a str, i64> = HashMap::new();
    o.iter().for_each(|r| *surplus.entry(r.core).or_default() += 1);
    n.iter().for_each(|r| *surplus.entry(r.core).or_default() -= 1);

    // For each hash: the tokens left out of the lines that went, and of the lines that came.
    type Left<'t> = Vec<(&'t str, usize)>;
    let mut holes: HashMap<u64, [Left; 2]> = HashMap::new();
    for (line, count) in surplus.into_iter().filter(|(_, count)| *count != 0) {
        let parts = tokens(line);
        let mut tried: Vec<&str> = Vec::new();
        for &token in parts.iter().filter(|t| parts.len() <= SWAP_MAX_TOKENS && !t.trim().is_empty()) {
            if tried.contains(&token) {
                continue;
            }
            tried.push(token);
            let mut hash = DefaultHasher::new();
            parts.iter().for_each(|&p| if p == token { 0u8.hash(&mut hash) } else { p.hash(&mut hash) });
            let left = &mut holes.entry(hash.finish()).or_default()[usize::from(count < 0)];
            // Two tokens are as good as many: the hash then pairs no line with another.
            let room = left.len() < 2;
            match left.iter_mut().find(|(t, _)| *t == token) {
                Some((_, lines)) => *lines += count.unsigned_abs() as usize,
                None if room => left.push((token, count.unsigned_abs() as usize)),
                None => {}
            }
        }
    }
    let mut swaps: HashMap<(&str, &str), usize> = HashMap::new();
    for [went, came] in holes.into_values() {
        if let ([(from, a)], [(to, b)]) = (&went[..], &came[..]) {
            *swaps.entry((*from, *to)).or_default() += *a.min(b);
        }
    }
    let mut found: Vec<_> = swaps.into_iter().filter(|(_, lines)| *lines >= REPEAT_MIN).collect();
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

    fn key<'k>(&self, core: &'k str) -> std::borrow::Cow<'k, str> {
        let parts = tokens(core);
        if !parts.iter().any(|t| self.stands_for.contains_key(*t)) {
            return core.into();
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
            for (p, q) in align(&x, &y, deadline) {
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
    for _ in 0..DISSOLVE_PASSES {
        let (mut islands, mut j) = (Vec::new(), 0);
        while j < map.n2o.len() {
            let Some(i) = map.n2o[j] else {
                j += 1;
                continue;
            };
            let len = 1 + (1..map.n2o.len() - j).take_while(|k| map.n2o[j + k] == Some(i + k)).count();
            if len <= ISLAND_MAX {
                let (old, new) = (around(&map.o2n, i, len), around(&map.n2o, j, len));
                let changed = [old.0, old.1, new.0, new.1].iter().all(|&rows| rows > 0);
                if changed && old.0.max(new.0) >= 2 * len && old.1.max(new.1) >= 2 * len {
                    islands.push((i, j, len));
                }
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

/// The replacements that were made, most frequent first, with the lines they were made on.
fn replacements(o: &[Row], n: &[Row], map: &Map) -> Vec<Replacement> {
    let mut count: HashMap<(&str, &str), usize> = HashMap::new();
    for (j, i) in map.n2o.iter().enumerate() {
        let Some(i) = *i else { continue };
        if o[i].core == n[j].core {
            continue;
        }
        let (x, y) = (tokens(o[i].core), tokens(n[j].core));
        let mut swaps: Vec<_> = x.iter().zip(&y).filter(|(p, q)| x.len() == y.len() && p != q).collect();
        swaps.sort();
        swaps.dedup();
        for (from, to) in swaps {
            *count.entry((*from, *to)).or_default() += 1;
        }
    }
    // The lines that still hold a token that was replaced: one pass over the new file.
    let mut left: HashMap<&str, usize> = count.keys().map(|(from, _)| (*from, 0)).collect();
    for row in n.iter().take(if left.is_empty() { 0 } else { n.len() }) {
        let mut held: Vec<&str> = tokens(row.core).into_iter().filter(|t| left.contains_key(t)).collect();
        held.sort();
        held.dedup();
        held.into_iter().for_each(|t| *left.entry(t).or_default() += 1);
    }
    let mut found: Vec<_> = count.into_iter().collect();
    found.sort_by(|a, b| b.1.cmp(&a.1).then(a.0.cmp(&b.0)));
    let replacement = |((from, to), lines): ((&str, &str), usize)| Replacement {
        from: from.to_string(),
        to: to.to_string(),
        lines,
        left: left[from],
    };
    found.into_iter().map(replacement).collect()
}

/// Lines of `rows` indented less than the first: how many, and the first of them.
fn outdented(rows: &[Row]) -> Option<(usize, usize)> {
    let mut less = rows.iter().filter(|r| r.width < rows[0].width);
    let first = less.next()?;
    Some((1 + less.count(), first.no))
}

/// For each row, the line it is under: the nearest row above it that is indented less.
fn headers(rows: &[Row]) -> Vec<Option<usize>> {
    let mut open: Vec<usize> = Vec::new();
    let mut under = |(i, row): (usize, &Row)| {
        while open.last().is_some_and(|&h| rows[h].width >= row.width) {
            open.pop();
        }
        open.push(i);
        open.len().checked_sub(2).map(|h| open[h])
    };
    rows.iter().enumerate().map(&mut under).collect()
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

/// A block of rows of the old file, of the new file, or of both.
fn block<'a>(kind: Kind, old: Option<&[Row]>, new: Option<&[Row]>, after: usize) -> Block<'a> {
    let span = |rows: &[Row]| (rows[0].no, rows[rows.len() - 1].no);
    // Text that is new has no block it could have run past, and no units to count.
    let whole = match kind {
        Kind::Inserted | Kind::Changed => None,
        _ => new.or(old),
    };
    // Only a block with a body has units: rows indented more than its first row.
    let units = whole.filter(|rows| kind != Kind::Reindented && rows.iter().any(|r| r.width > rows[0].width));
    let starts = |rows: &[Row]| {
        let head = |pair: &&[Row]| pair[1].width == rows[0].width && pair[1].no - pair[0].no > 1;
        rows.windows(2).filter(head).map(|pair| pair[1].no).collect()
    };
    let (heads, outdented) = (units.map_or(Vec::new(), starts), whole.and_then(outdented));
    Block { kind, old: old.map(span), new: new.map(span), indent: Indent::Same, after, outdented, passed: None, heads }
}

/// What the block that moved from old row `i` to new row `j` passed on its way: the rows that
/// stayed and are on its other side now. `stayed` holds the rows that stayed, each as its old
/// and its new row. They are in the order of both files, so two searches find the rows.
fn passed(n: &[Row], stayed: &[(usize, usize)], i: usize, j: usize) -> Option<(bool, Span)> {
    let span = |rows: &[(usize, usize)]| (n[rows[0].1].no, n[rows[rows.len() - 1].1].no);
    let (above, below) = stayed.split_at(stayed.partition_point(|&(_, new)| new < j));
    // Above it now and below it before: it went down past them.
    let down = &above[above.partition_point(|&(old, _)| old < i)..];
    let up = &below[..below.partition_point(|&(old, _)| old < i)];
    match (down.is_empty(), up.is_empty()) {
        (false, _) => Some((true, span(down))),
        (true, false) => Some((false, span(up))),
        (true, true) => None,
    }
}

/// The blocks, in the order of the new file.
fn blocks<'a>(o: &[Row<'a>], n: &[Row<'a>], map: &Map) -> Vec<Block<'a>> {
    let above = |j: usize| if j == 0 { 0 } else { n[j - 1].no };
    let stays = |j: &usize| map.stays[*j];
    let mut found = Vec::new();

    // A block ran past the end of the block it started in if it holds lines indented less than
    // its first line, and the line that its first line was under did not go with it. That line
    // must still be there, and not moved or re-indented as the block was. If it changed or
    // went as well, the block may hold whole units, and its first line is just not the first
    // line of one: three functions put into a new class start at the body of the first.
    let under = headers(o);
    let ran_past = |first: usize, b: &Block| {
        let header = under[first].and_then(|h| map.o2n[h].map(|j| (h, j)));
        header.is_some_and(|(h, j)| match b.kind {
            Kind::Deleted => true,
            Kind::Moved => map.stays[j],
            _ => shift(o[h].text, n[j].text) != b.indent,
        })
    };
    let checked = |first: usize, b: Block<'a>| Block { outdented: b.outdented.filter(|_| ran_past(first, &b)), ..b };

    // Rows that are in both files: moved, or re-indented where they were.
    let stayed: Vec<(usize, usize)> = (0..n.len()).filter(stays).filter_map(|j| Some((map.n2o[j]?, j))).collect();
    let mut j = 0;
    while j < n.len() {
        let Some(i) = map.n2o[j] else {
            j += 1;
            continue;
        };
        let (stay, indent) = (map.stays[j], shift(o[i].text, n[j].text));
        let same = |k: &usize| {
            let (a, b) = (i + k, j + k);
            b < n.len() && map.n2o[b] == Some(a) && map.stays[b] == stay && shift(o[a].text, n[b].text) == indent
        };
        let len = 1 + (1..).take_while(same).count();
        let (old, new) = (Some(&o[i..i + len]), Some(&n[j..j + len]));
        if !stay {
            let moved = Block { indent, passed: passed(n, &stayed, i, j), ..block(Kind::Moved, old, new, above(j)) };
            found.push(checked(i, moved));
        } else if indent != Indent::Same {
            found.push(checked(i, Block { indent, ..block(Kind::Reindented, old, new, above(j)) }));
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
    let inserted = runs(&map.n2o);
    let mut used = vec![false; inserted.len()];
    for gone in runs(&map.o2n) {
        // The rows of the new file that the rows around the run are now, if they stayed. A
        // neighbor that moved away says nothing about the place the run left.
        let paired = |i: usize| map.o2n[i].expect("the rows around a run are paired");
        let prev = gone.start.checked_sub(1).map(paired);
        let next = (gone.end < o.len()).then(|| paired(gone.end));
        // The run of new rows that starts below the row above, or ends above the row below.
        let start = if gone.start == 0 { Some(0) } else { prev.filter(stays).map(|j| j + 1) };
        let end = if gone.end == o.len() { Some(n.len()) } else { next.filter(stays) };
        let starts = start.and_then(|at| inserted.binary_search_by_key(&at, |run| run.start).ok());
        let ends = end.and_then(|at| inserted.binary_search_by_key(&at, |run| run.end).ok());
        if let Some(k) = [starts, ends].into_iter().flatten().find(|&k| !used[k]) {
            used[k] = true;
            let run = inserted[k].clone();
            found.push(block(Kind::Changed, Some(&o[gone]), Some(&n[run.clone()]), above(run.start)));
            continue;
        }
        // The place the run left, in the new file: next to a neighbor that stayed, if one did.
        let after = match (prev.filter(stays), next.filter(stays), prev, next) {
            (Some(j), ..) | (None, None, Some(j), _) => n[j].no,
            (None, Some(j), ..) | (None, None, None, Some(j)) => above(j),
            (None, None, None, None) => 0,
        };
        // Two or three units deleted together are reported one by one, more as one block.
        let units = Some(pieces(o, gone.clone())).filter(|units| units.len() <= SPLIT_MAX).unwrap_or(vec![gone]);
        let deleted = |unit: Range<usize>| checked(unit.start, block(Kind::Deleted, Some(&o[unit]), None, after));
        found.extend(units.into_iter().map(deleted));
    }
    for (run, _) in inserted.iter().zip(&used).filter(|(_, used)| !**used) {
        found.push(block(Kind::Inserted, None, Some(&n[run.clone()]), above(run.start)));
    }
    found.sort_by_key(|b| match b.new {
        Some((first, _)) => (first, 0),
        None => (b.after, 1),
    });
    found
}

/// The runs of blank lines whose length changed.
///
/// A warning has to mean that something is very likely wrong, so it is kept for the place
/// where two blocks of the old file were joined and the blank lines between them are not
/// what either of them had. Next to new text, less is known, and a note is the most it gets.
fn spacing(o: &[Row], n: &[Row], map: &Map) -> Vec<Spacing> {
    // The blank lines above row i. None above the first row and below the last one: the ends
    // of a file say nothing about the space between two blocks.
    let gap = |rows: &[Row], i: usize| (i > 0 && i < rows.len()).then(|| rows[i].no - rows[i - 1].no - 1);
    let mut found = Vec::new();
    if let (Some(a), Some(b)) = (o.first(), n.first()) {
        if a.no != b.no {
            found.push(Spacing { warn: b.no > a.no, above: 0, now: b.no - 1, was: vec![a.no - 1] });
        }
    }
    let mut j = 0;
    while j < n.len() {
        if map.n2o[j].is_none() {
            // New text, in rows j to end - 1. The run above it and the run below it are
            // compared with the run that was there, if there was one. If one of the two is as
            // it was, the text was put next to that run, and nothing is wrong.
            let end = j + (j..n.len()).take_while(|&k| map.n2o[k].is_none()).count();
            let top = j.checked_sub(1).and_then(|k| Some((n[k].no, gap(n, j)?, gap(o, map.n2o[k]? + 1)?)));
            let bottom = map.n2o.get(end).and_then(|q| Some((n[end - 1].no, gap(n, end)?, gap(o, (*q)?)?)));
            let sides: Vec<_> = top.into_iter().chain(bottom).filter(|side| side.2 > 0).collect();
            let changed = |&(above, now, was): &(usize, usize, usize)| Spacing { warn: false, above, now, was: vec![was] };
            found.extend(sides.iter().filter(|_| sides.iter().all(|side| side.1 != side.2)).map(changed));
            j = end;
            continue;
        }
        if let (Some(p), Some(q), Some(now)) = (j.checked_sub(1).and_then(|k| map.n2o[k]), map.n2o[j], gap(n, j)) {
            let (warn, mut was): (bool, Vec<usize>) = if q == p + 1 {
                // The same two lines as before, with other space between them.
                (false, gap(o, q).into_iter().collect())
            } else {
                // Two blocks joined here: one that ends in row j - 1 and one that starts in
                // row j. The rows of a block are paired one by one. A block is a unit if its
                // first row has rows under it, as the first line of a function has.
                let linked = |k: &usize| map.n2o[*k].is_some_and(|i| map.n2o[k + 1] == Some(i + 1));
                let first = (0..j - 1).rev().take_while(linked).last().unwrap_or(j - 1);
                let last = (j..n.len() - 1).take_while(linked).last().map_or(j, |k| k + 1);
                let unit = |row: usize, end: usize| row < end && n[row + 1].width > n[row].width;
                let (above, below) = (unit(first, j - 1), unit(j, last));
                match (gap(o, p + 1), gap(o, q)) {
                    // The space should be what one of the two had there.
                    (Some(under), Some(over)) => (true, vec![under, over]),
                    // One of them was at an end of the old file, which says nothing. What the
                    // other one had is a reason to warn if that one is a unit.
                    (Some(under), None) => (above, vec![under]),
                    (None, Some(over)) => (below, vec![over]),
                    // Both were. What they had on their other sides is the next best thing.
                    (None, None) => {
                        let over = map.n2o[first].and_then(|i| gap(o, i));
                        let under = map.n2o[last].and_then(|i| gap(o, i + 1));
                        (above || below, over.into_iter().chain(under).collect())
                    }
                }
            };
            was.dedup();
            if !was.is_empty() && !was.contains(&now) {
                found.push(Spacing { warn, above: n[j - 1].no, now, was });
            }
        }
        j += 1;
    }
    found
}

/// The lines that are the same line before and after, as line numbers: the rows that are paired,
/// and the blank lines between two rows that were next to each other and still are.
fn same_lines(o: &[Row], n: &[Row], map: &Map, lines: (usize, usize)) -> Vec<(usize, usize)> {
    let mut pairs = Vec::new();
    for (j, i) in map.n2o.iter().enumerate().filter_map(|(j, i)| Some((j, (*i)?))) {
        let shared = if i == 0 || j == 0 { i == j } else { map.n2o[j - 1] == Some(i - 1) };
        if shared {
            let (old, new) = if i == 0 { (0, 0) } else { (o[i - 1].no, n[j - 1].no) };
            pairs.extend((old + 1..o[i].no).zip(new + 1..n[j].no));
        }
        pairs.push((o[i].no, n[j].no));
    }
    // The blank lines below the last rows, or all lines of two files of blank lines.
    let below = match (o.last(), n.last()) {
        (Some(a), Some(b)) if map.n2o[n.len() - 1] == Some(o.len() - 1) => Some((a.no, b.no)),
        (None, None) => Some((0, 0)),
        _ => None,
    };
    pairs.extend(below.into_iter().flat_map(|(old, new)| (old + 1..=lines.0).zip(new + 1..=lines.1)));
    pairs
}

/// What changed in lines that look the same: their endings, and whitespace that does not show.
fn unseen(old: (&[&str], &[&str]), new: (&[&str], &[&str]), pairs: &[(usize, usize)]) -> Unseen {
    let name = |end: &str| if end == "\n" { "LF" } else { "CRLF" };
    let tail = |line: &str| line.len() - line.trim_end().len();
    let count = |slot: &mut Option<(usize, usize)>, line: usize| {
        *slot = Some(slot.map_or((1, line), |(lines, first)| (lines + 1, first.min(line))));
    };
    let mut found = Unseen::default();
    for &(a, b) in pairs {
        let (from, to) = (old.1[a - 1], new.1[b - 1]);
        if from != to && !from.is_empty() && !to.is_empty() {
            match found.endings.iter_mut().find(|e| (e.0, e.1) == (name(from), name(to))) {
                Some((.., lines)) => *lines += 1,
                None => found.endings.push((name(from), name(to), 1)),
            }
        }
        let (was, is) = (old.0[a - 1], new.0[b - 1]);
        if was.trim().is_empty() && was != is {
            count(&mut found.blank, b);
        } else if was[was.len() - tail(was)..] != is[is.len() - tail(is)..] {
            count(&mut found.trailing, b);
        }
    }
    found.endings.sort_by(|a, b| b.2.cmp(&a.2));
    found
}

/// Compares two texts. The work stops being exact at the deadline, and is not begun after it.
pub fn analyze<'a>(old_text: &'a str, new_text: &'a str, deadline: Instant) -> Analysis<'a> {
    let ((old, old_ends), (new, new_ends)) = (split(old_text), split(new_text));
    let end_newlines = (newlines_at_end(&old, &old_ends), newlines_at_end(&new, &new_ends));
    let nothing = (Vec::new(), Vec::new(), Vec::new(), Unseen::default());
    if Instant::now() >= deadline {
        let (replacements, blocks, spacing, unseen) = nothing;
        return Analysis { old, new, replacements, blocks, spacing, unseen, end_newlines, timed_out: true };
    }
    let (o, n) = (rows(&old), rows(&new));
    let canon = Canon::new(&discover(&o, &n));
    let mut ids = HashMap::new();
    let ok: Vec<u32> = o.iter().map(|r| intern(&mut ids, canon.key(r.core))).collect();
    let nk: Vec<u32> = n.iter().map(|r| intern(&mut ids, canon.key(r.core))).collect();

    let mut map = Map { o2n: vec![None; o.len()], n2o: vec![None; n.len()], stays: vec![true; n.len()] };
    let mut tiles = claim_runs(&o, &n, &ok, &nk, &mut map);
    let stay = heaviest_in_order(&tiles, &n);
    grow(&mut tiles, &stay, &o, &n, &ok, &nk, &mut map);
    for (t, _) in tiles.iter().zip(&stay).filter(|(_, stay)| !**stay) {
        map.stays[t.new..t.new + t.len].fill(false);
    }
    align_rest(&tiles, &stay, &ok, &nk, &mut map, deadline);
    let timed_out = Instant::now() >= deadline;
    dissolve(&mut map);

    let pairs = same_lines(&o, &n, &map, (old.len(), new.len()));
    Analysis {
        replacements: replacements(&o, &n, &map),
        blocks: blocks(&o, &n, &map),
        spacing: spacing(&o, &n, &map),
        unseen: unseen((&old, &old_ends), (&new, &new_ends), &pairs),
        end_newlines,
        timed_out,
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

/// The first line of a block, to name it, and how many more lines like it the block holds.
fn label(text: &[&str], span: Span, peers: usize) -> String {
    let more = if peers == 0 { String::new() } else { format!(" +{peers} more at this indent") };
    format!("  ({}){more}", cut(text[span.0 - 1].trim(), 60))
}

/// What happened to a block, in one line. Two more lines follow where they apply: what a moved
/// block passed, and a warning if the block ran past the end of the block it started in.
fn headlines(a: &Analysis, b: &Block) -> Vec<String> {
    // A block can hold more blank lines, or fewer, than it did. Both sizes are given then.
    let sizes = |o: Span, n: Span| {
        let to = if o.1 - o.0 == n.1 - n.0 { String::new() } else { format!(" to {}", n.1 - n.0 + 1) };
        format!("{}{to}: {} -> {}", lines(o), range(o), range(n))
    };
    let first = match (b.kind, b.old, b.new) {
        (Kind::Deleted, Some(o), _) => format!("deleted {}: {}{}", lines(o), range(o), label(&a.old, o, b.heads.len())),
        (Kind::Inserted, _, Some(n)) => format!("inserted {}: {}{}", lines(n), range(n), label(&a.new, n, 0)),
        (Kind::Changed, Some(o), Some(n)) => format!("changed {}{}", sizes(o, n), label(&a.new, n, 0)),
        (_, Some(o), Some(n)) => {
            let verb = if b.kind == Kind::Moved { "moved" } else { "reindented" };
            format!("{verb} {}{}{}", sizes(o, n), indent_text(b.indent), label(&a.new, n, b.heads.len()))
        }
        _ => String::new(),
    };
    let passed = b.passed.map(|(down, span)| {
        let way = if down { "down" } else { "up" };
        format!("  {way} past {}: {}{}", lines(span), range(span), label(&a.new, span, 0))
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

/// The runs of blank lines that are warnings, or those that are not: the first `most`, and how
/// many more there are.
fn spacing_lines(a: &Analysis, warn: bool, most: usize) -> Vec<String> {
    let all: Vec<_> = a.spacing.iter().filter(|s| s.warn == warn).collect();
    let more = all.len().checked_sub(most + 1).map(|more| {
        format!("{}{} of blank lines changed", if warn { "WARNING: " } else { "spacing: " }, count(more + 1, "more run"))
    });
    all.iter().take(most).map(|s| spacing_text(s)).chain(more).collect()
}

/// The warnings about the file as a whole: how it ends, and where blank lines went wrong.
fn file_warnings(a: &Analysis) -> Vec<String> {
    let (before, after) = a.end_newlines;
    let blank = match after {
        0 | 1 => String::new(),
        n => format!(" ({} at the end)", count(n - 1, "blank line")),
    };
    let end = format!("WARNING: file ends with {}, was {before}{blank}", count(after, "newline"));
    Some(end).filter(|_| before != after).into_iter().chain(spacing_lines(a, true, MAX_WARNINGS)).collect()
}

/// The changes that a diff shows as two lines that look the same.
fn unseen_lines(unseen: &Unseen) -> Vec<String> {
    let ending = |(from, to, lines): &(&str, &str, usize)| format!("line endings: {from} -> {to} on {}", count(*lines, "line"));
    let endings = unseen.endings.iter().map(ending);
    let blank = unseen.blank.map(|(lines, first)| format!("whitespace-only lines changed: {lines}, from +{first}"));
    let trailing = unseen.trailing.map(|(lines, first)| {
        format!("trailing whitespace changed on {}, from +{first}", count(lines, "line"))
    });
    endings.chain(blank).chain(trailing).collect()
}

/// The replacements, the first `MAX_REPLACEMENTS` by name and the others counted.
fn replacement_lines(a: &Analysis) -> Vec<String> {
    let line = |r: &Replacement| {
        let left = match r.left {
            0 => "none left".to_string(),
            n => format!("{} still {} {}", count(n, "line"), if n == 1 { "has" } else { "have" }, r.from),
        };
        format!("replaced on {}: {} -> {} ({left})", count(r.lines, "line"), r.from, r.to)
    };
    let rest = a.replacements.get(MAX_REPLACEMENTS..).unwrap_or_default();
    let more = Some(rest).filter(|rest| !rest.is_empty()).map(|rest| {
        let lines = rest.iter().map(|r| r.lines).sum::<usize>();
        format!("... {} on {}", count(rest.len(), "more replacement"), count(lines, "line"))
    });
    a.replacements.iter().take(MAX_REPLACEMENTS).map(line).chain(more).collect()
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
/// the block holds. Those are the lines indented less than the first line, if there are any,
/// and else the lines where the other units of the block start.
fn shown(text: &[&str], (first, last): Span, level: &Level, heads: &[usize]) -> Vec<usize> {
    if last - first < 2 * level.edge + 1 {
        return (first..=last).collect();
    }
    let mut picks: Vec<usize> = (first..first + level.edge).chain(last + 1 - level.edge..=last).collect();
    let inner = || (first + 1..=last).filter(|&no| !text[no - 1].trim().is_empty());
    let least = inner().map(|no| width(text[no - 1])).min().unwrap_or(0);
    let marks: Vec<usize> = match least < width(text[first - 1]) {
        true => inner().filter(|&no| width(text[no - 1]) == least).take(level.marks + 2 * level.edge).collect(),
        false => heads.to_vec(),
    };
    let marks: Vec<usize> = marks.into_iter().filter(|no| !picks.contains(no)).take(level.marks).collect();
    picks.extend(marks);
    picks.sort();
    picks
}

fn excerpt(a: &Analysis, b: &Block, level: &Level) -> Vec<String> {
    let mut out = Vec::new();
    if level.edge == 0 {
        return out;
    }
    // A long line that replaced another may differ from it only past the place where the
    // excerpt is cut. Both are then shown from a little before the difference.
    let pairs = match (b.kind, b.old, b.new) {
        (Kind::Changed, Some(old), Some(new)) if old.1 - old.0 == new.1 - new.0 => Some((old.0, new.0)),
        _ => None,
    };
    let skipped = |k: usize| {
        let (old, new) = pairs.map_or(("", ""), |(old, new)| (a.old[old + k - 1], a.new[new + k - 1]));
        let same = old.chars().zip(new.chars()).take_while(|(x, y)| x == y).count();
        if same + 20 > MAX_WIDTH { same - 20 } else { 0 }
    };
    let mut side = |text: &[&str], sign: char, span: Span| {
        let mut last = span.0;
        for no in shown(text, span, level, &b.heads) {
            if no > last + 1 {
                out.push("   ...".to_string());
            }
            let line = match skipped(no - span.0) {
                0 => text[no - 1].to_string(),
                skip => format!("...{}", text[no - 1].chars().skip(skip).collect::<String>()),
            };
            out.push(cut(&format!("  {sign}{no}:{line}"), MAX_WIDTH));
            last = no;
        }
    };
    if let (Some(span), true) = (b.old, b.new.is_none() || b.kind == Kind::Changed) {
        side(&a.old, '-', span);
    }
    if let Some(span) = b.new {
        side(&a.new, '+', span);
    }
    if level.context {
        let line = |no: usize| cut(&format!("   {no}:{}", a.new[no - 1]), MAX_WIDTH);
        if b.after > 0 {
            out.insert(0, line(b.after));
        }
        let (end, last) = (b.new.map_or(b.after, |n| n.1), a.new.len());
        let next = (end + 1..=last).find(|&no| !a.new[no - 1].trim().is_empty());
        out.push(match next {
            Some(no) => line(no),
            None if end < last => format!("   (end of file, after {})", count(last - end, "blank line")),
            None => "   (end of file)".to_string(),
        });
    }
    out
}

/// The summary: at most `OUTPUT_MAX_LINES` lines, each of which ends with a newline.
pub fn render(a: &Analysis, name: &str, totals: &Totals) -> String {
    let Totals { added, removed, hunks, rough } = totals;
    let (hunks, before, after) = (count(*hunks, "hunk"), a.old.len(), a.new.len());
    let first = match *rough {
        false => format!("{name}: +{added} -{removed} lines in {hunks}; {before} -> {after} lines"),
        true => format!("{name}: about +{added} -{removed} lines, counted roughly; {before} -> {after} lines"),
    };
    let mut top = vec![first, LEGEND.to_string()];
    top.extend(a.timed_out.then(|| "note: the time ran out. What follows is what was found by then.".to_string()));
    let told = top.len();
    top.extend(file_warnings(a));
    top.extend(unseen_lines(&a.unseen));
    top.extend(spacing_lines(a, false, MAX_NOTES));
    top.extend(replacement_lines(a));
    if top.len() == told && a.blocks.is_empty() && *added + *removed > 0 {
        top.push("the lines that changed are not described here; --diff full prints them".to_string());
    }

    let room = OUTPUT_MAX_LINES.saturating_sub(top.len());
    let build = |level: &Level| -> Vec<Vec<String>> {
        a.blocks.iter().map(|b| headlines(a, b).into_iter().chain(excerpt(a, b, level)).collect()).collect()
    };
    let fits = |body: &Vec<Vec<String>>| body.iter().map(Vec::len).sum::<usize>() <= room;
    let body = match a.blocks.len() > room {
        // More blocks than lines: there is no room for an excerpt, nor for every block.
        true => a.blocks.iter().take(room).map(|b| headlines(a, b)).collect(),
        false => LEVELS.iter().map(build).find(fits).unwrap_or_else(|| build(&LEVELS[LEVELS.len() - 1])),
    };

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

/// The warnings of the summary on their own, to follow a diff that is printed in full: the
/// warnings about the file, and each block that has a warning. Empty if there is none.
pub fn warnings(a: &Analysis) -> String {
    let flagged = a.blocks.iter().filter(|b| b.outdented.is_some()).take(MAX_WARNINGS);
    let late = a.timed_out.then(|| "note: the time ran out before spacing and indentation were checked".to_string());
    let lines = file_warnings(a).into_iter().chain(flagged.flat_map(|b| headlines(a, b))).chain(late);
    lines.map(|l| format!("{l}\n")).collect()
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
        analyze(old.into().leak(), new.into().leak(), Instant::now() + Duration::from_secs(60))
    }

    fn totals() -> Totals {
        Totals { added: 0, removed: 0, hunks: 0, rough: false }
    }

    #[test]
    fn counts_the_newlines_at_the_end() {
        let count = |text: &str| {
            let (lines, ends) = split(text);
            newlines_at_end(&lines, &ends)
        };
        assert_eq!([count(""), count("a"), count("a\n"), count("a\n\n\n"), count("a\r\n\r\n")], [0, 0, 1, 3, 2]);
        // A line of spaces at the end is a blank line at the end.
        assert_eq!([count("a\n  \n"), count("a\n  "), count("\n\n")], [2, 1, 2]);
        assert_eq!(split("a\r\nb\n\nc"), (vec!["a", "b", "", "c"], vec!["\r\n", "\n", "\n", ""]));
    }

    #[test]
    fn splits_a_line_into_tokens() {
        assert_eq!(tokens("self.log_event(\"x\",  1)"), ["self", ".", "log_event", "(", "\"", "x", "\"", ",", "  ", "1", ")"]);
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
    fn units_deleted_together_are_told_apart_or_counted() {
        let units = |names: &[&str]| names.iter().map(|f| format!("def {f}():\n    return {f}\n\n")).collect::<String>();
        let (top, end) = (part("top", 3), part("end", 3));
        let found = compare(format!("{top}\n{}{end}", units(&["a", "b"])), format!("{top}\n{end}"));
        assert_eq!(kinds(&found), [(Kind::Deleted, Some((5, 6)), None), (Kind::Deleted, Some((8, 9)), None)]);
        assert!(found.spacing.is_empty());

        let found = compare(format!("{top}\n{}{end}", units(&["a", "b", "c", "d", "e"])), format!("{top}\n{end}"));
        assert_eq!(kinds(&found), [(Kind::Deleted, Some((5, 18)), None)]);
        let text = render(&found, "f.py", &totals());
        assert!(text.contains("deleted 14 lines: 5-18  (def a():) +4 more at this indent\n"), "{text}");
        assert!(text.contains("  -8:def b():\n"), "{text}");
    }

    #[test]
    fn the_excerpt_says_where_the_file_ends() {
        let found = compare("a = 1\nb = 2\n", "a = 1\nb = 2\nc = 3\n\n\n");
        let text = render(&found, "f.py", &totals());
        assert!(text.ends_with("   2:b = 2\n  +3:c = 3\n   (end of file, after 2 blank lines)\n"), "{text}");
        assert!(text.contains("WARNING: file ends with 3 newlines, was 1 (2 blank lines at the end)\n"), "{text}");
    }

    #[test]
    fn a_change_far_into_a_long_line_is_shown() {
        let start = "word ".repeat(40);
        let found = compare(format!("a = 1\n{start}old end\nb = 2\n"), format!("a = 1\n{start}new end\nb = 2\n"));
        let text = render(&found, "f.md", &totals());
        assert!(text.contains("  -2:...word word word word old end\n  +2:...word word word word new end\n"), "{text}");
    }

    #[test]
    fn a_long_block_shows_its_first_and_last_lines() {
        let (a, b) = (part("alpha", 5), class("Gone", 30));
        let found = compare(format!("{a}{b}{a}"), format!("{a}{a}"));
        let text = render(&found, "f.py", &totals());
        let expected = "deleted 30 lines: 6-35  (class Gone:)\n   5:alpha 5\n  -6:class Gone:\n  -7:    Gone_1 = 1\n   ...\n  -34:    Gone_28 = 28\n  -35:    Gone_29 = 29\n   6:alpha 1\n";
        assert!(text.ends_with(expected), "{text}");
    }

    #[test]
    fn a_replacement_is_found_wherever_the_line_went() {
        // Every line that was renamed also moved, so no diff puts the old line next to the new.
        let (a, b) = (part("alpha", 6), part("beta", 6));
        let calls = |name: &str| (0..5).map(|i| format!("    {name}(\"step\", {i})\n")).collect::<String>();
        let found = compare(format!("{a}{}{b}", calls("log_event")), format!("{a}{b}{}", calls("emit_event")));
        assert_eq!(found.replacements, [Replacement { from: "log_event".into(), to: "emit_event".into(), lines: 5, left: 0 }]);
        assert_eq!(kinds(&found), [(Kind::Moved, Some((7, 11)), Some((13, 17)))]);
    }

    #[test]
    fn replacements_past_the_first_five_are_counted() {
        // Seven renames, on 10, 9, 8, 7, 6, 5 and 4 lines.
        let names = ["aa", "bb", "cc", "dd", "ee", "ff", "gg"];
        let text = |end: &str| {
            let lines = |(k, name): (usize, &&str)| (0..10 - k).map(|i| format!("x{k}_{i} = {name}_{end}({i})\n")).collect::<String>();
            names.iter().enumerate().map(lines).collect::<String>()
        };
        let found = compare(text("old"), text("new"));
        let lines: Vec<usize> = found.replacements.iter().map(|r| r.lines).collect();
        assert_eq!(lines, [10, 9, 8, 7, 6, 5, 4]);
        assert!(found.blocks.is_empty());
        let text = render(&found, "f.py", &totals());
        assert!(text.contains("replaced on 6 lines: ee_old -> ee_new (none left)\n... 2 more replacements on 9 lines\n"), "{text}");
        assert!(!text.contains("ff_old"), "{text}");
    }

    #[test]
    fn a_rename_on_a_large_file_is_found() {
        // 80,000 lines, a rename on every other one, and the first five lines deleted.
        let line = |name: &str, i: usize| if i % 2 == 0 { format!("v{i} = {name}({i})\n") } else { format!("w{i} = {i}\n") };
        let old: String = (0..80_000).map(|i| line("log_event", i)).collect();
        let new: String = (5..80_000).map(|i| line("emit_event", i)).collect();
        let found = compare(old, new);
        assert_eq!(found.replacements, [Replacement { from: "log_event".into(), to: "emit_event".into(), lines: 39_997, left: 0 }]);
        assert_eq!(kinds(&found), [(Kind::Deleted, Some((1, 5)), None)]);
    }

    #[test]
    fn line_endings_that_changed_are_reported() {
        let old: String = (0..100).map(|i| format!("line {i}\r\n{}", if i % 10 == 9 { "\r\n" } else { "" })).collect();
        let found = compare(&old, old.replace("\r\n", "\n"));
        assert_eq!(found.unseen, Unseen { endings: vec![("CRLF", "LF", 110)], blank: None, trailing: None });
        assert!(found.blocks.is_empty() && found.spacing.is_empty());
        let text = render(&found, "f.txt", &Totals { added: 110, removed: 110, hunks: 1, rough: false });
        assert!(text.ends_with("in the new file.\nline endings: CRLF -> LF on 110 lines\n"), "{text}");
    }

    #[test]
    fn whitespace_that_does_not_show_is_reported() {
        let old: String = (0..100).map(|i| format!("line {i}\n   \n")).collect();
        let found = compare(&old, old.replace("   \n", "\n"));
        assert_eq!(found.unseen, Unseen { endings: vec![], blank: Some((100, 2)), trailing: None });
        assert!(render(&found, "f.txt", &totals()).contains("whitespace-only lines changed: 100, from +2\n"));

        let old: String = (0..8).map(|i| format!("    v{i} = {i}  \n")).collect();
        let found = compare(&old, old.replace("  \n", "\n"));
        assert_eq!(found.unseen, Unseen { endings: vec![], blank: None, trailing: Some((8, 1)) });
        assert!(found.blocks.is_empty());
        assert!(render(&found, "f.txt", &totals()).contains("trailing whitespace changed on 8 lines, from +1\n"));
    }

    #[test]
    fn the_header_is_never_all_there_is() {
        // Nothing the analysis knows of, and lines that changed all the same.
        let found = compare("a = 1\n", "a = 1\n");
        let text = render(&found, "f.py", &Totals { added: 1, removed: 1, hunks: 1, rough: false });
        assert!(text.ends_with("the lines that changed are not described here; --diff full prints them\n"), "{text}");
    }

    #[test]
    fn new_text_next_to_a_run_that_is_as_it_was_is_no_warning() {
        // A method added at the end of a class: one blank line above it, as between methods,
        // and the two blank lines that ended the class below it.
        let old = "class A:\n    def m1(self):\n        return 1\n\n    def m2(self):\n        return 2\n\n\ndef top():\n    return 0\n";
        let new = old.replace("\n\n\ndef top", "\n\n    def m3(self):\n        return 3\n\n\ndef top");
        let found = compare(old, &new);
        assert_eq!(kinds(&found), [(Kind::Inserted, None, Some((8, 9)))]);
        assert!(found.spacing.is_empty(), "{:?}", found.spacing);

        // Neither run is as it was: a note, and still no warning.
        let new = old.replace("\n\n\ndef top", "\n\n    def m3(self):\n        return 3\n\ndef top");
        let found = compare(old, &new);
        let note = |above: usize| Spacing { warn: false, above, now: 1, was: vec![2] };
        assert_eq!(found.spacing, [note(6), note(9)]);
        assert!(warnings(&found).is_empty());
    }

    #[test]
    fn blocks_put_into_a_new_block_did_not_run_past_it() {
        let old = "import os\n\n\ndef a():\n    return 1\n\n\ndef b():\n    return 2\n\n\ndef c():\n    return 3\n";
        let new = "import os\n\n\nclass K:\n    def a(self):\n        return 1\n\n    def b():\n        return 2\n\n    def c():\n        return 3\n";
        let found = compare(old, new);
        assert_eq!(kinds(&found), [(Kind::Changed, Some((4, 4)), Some((4, 5))), (Kind::Reindented, Some((5, 13)), Some((6, 12)))]);
        assert_eq!(found.blocks[1].outdented, None);
        let text = render(&found, "f.py", &totals());
        assert!(text.contains("reindented 9 lines to 7: 5-13 -> 6-12, indent +4 spaces  (return 1)\n"), "{text}");
        assert!(!text.contains("WARNING"), "{text}");
    }

    #[test]
    fn warns_about_the_last_block_moved_to_the_top_with_no_blank_line_below_it() {
        let old = "def a():\n    return 1\n\n\ndef b():\n    return 2\n\n\ndef c():\n    return 3\n";
        let new = "def c():\n    return 3\ndef a():\n    return 1\n\n\ndef b():\n    return 2\n\n\n";
        let found = compare(old, new);
        assert_eq!(kinds(&found), [(Kind::Moved, Some((9, 10)), Some((1, 2)))]);
        assert_eq!(found.spacing, [Spacing { warn: true, above: 2, now: 0, was: vec![2] }]);
        let expected = "WARNING: file ends with 3 newlines, was 1 (2 blank lines at the end)\nWARNING: no blank line between 2 and 3, was 2\n";
        assert_eq!(warnings(&found), expected);
    }

    #[test]
    fn one_line_moved_to_the_end_of_the_file_is_no_warning() {
        // It had a blank line above it and has none now. The line it follows was the last one
        // of the file, so nothing is known about the space below that, and a line on its own
        // is not a unit that has to keep its distance.
        let found = compare("a = 1\n\nb = 2\nc = 3\nd = 4\n", "a = 1\n\nc = 3\nd = 4\nb = 2\n");
        assert_eq!(kinds(&found), [(Kind::Moved, Some((3, 3)), Some((5, 5)))]);
        assert_eq!(found.spacing, [Spacing { warn: false, above: 4, now: 0, was: vec![1] }]);
        assert!(warnings(&found).is_empty());
    }

    /// Time is measured only in a build like the one that is released.
    #[cfg(not(debug_assertions))]
    #[test]
    fn many_blocks_take_time_in_proportion() {
        // Every line is a block: all lines in reverse order, or every other line changed.
        let lines = |count: usize| (0..count).map(|i| format!("line {i}\n"));
        let reversed = |count: usize| (lines(count).collect::<String>(), lines(count).rev().collect::<String>());
        let changed = |count: usize| {
            let other = |(i, line): (usize, String)| if i % 2 == 0 { line.replace('\n', " + more\n") } else { line };
            (lines(count).collect::<String>(), lines(count).enumerate().map(other).collect::<String>())
        };
        let time = |(old, new): (String, String), blocks: usize| {
            let started = Instant::now();
            let found = compare(old, new);
            let text = render(&found, "f.txt", &totals());
            assert_eq!(found.blocks.len(), blocks);
            assert!(text.lines().count() <= OUTPUT_MAX_LINES && text.contains(" blocks not shown; "), "{text}");
            started.elapsed()
        };
        let times = [
            (time(reversed(30_000), 29_999), time(reversed(120_000), 119_999)),
            (time(changed(30_000), 15_000), time(changed(120_000), 60_000)),
        ];
        // Four times the lines take four times as long, and sixteen times as long if each
        // block is compared with every other. On a slow machine only the ratio tells.
        for (small, large) in times {
            assert!(large < Duration::from_secs(3) || large < small * 10, "{small:?} for 30,000 lines, {large:?} for 120,000");
        }
    }

    #[test]
    fn nothing_is_begun_after_the_deadline() {
        let (a, b, c) = (part("alpha", 10), part("beta", 4), part("gamma", 10));
        let (old, new) = (format!("{a}{b}{c}"), format!("{a}{c}{b}\n\n"));
        let found = analyze(&old, &new, Instant::now());
        assert!(found.timed_out && found.blocks.is_empty());
        let text = render(&found, "f.py", &Totals { added: 6, removed: 4, hunks: 2, rough: true });
        let expected = "f.py: about +6 -4 lines, counted roughly; 24 -> 26 lines\n\
            summary (--diff full prints the diff). -N: old line. +N: new line. N: line next to the block, in the new file.\n\
            note: the time ran out. What follows is what was found by then.\n\
            WARNING: file ends with 3 newlines, was 1 (2 blank lines at the end)\n";
        assert_eq!(text, expected);
    }
}
