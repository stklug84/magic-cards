//! The terminal frame (the rendering half of the Python `tui.py`, on
//! ratatui): header, player boards, stack / combat, the event log and
//! the status line - and the interactive replay app on top of it.

use std::fmt::Write as _;
use std::time::Duration;

use ratatui::layout::{Constraint, Layout, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span, Text};
use ratatui::widgets::{Block, Paragraph, Wrap};
use ratatui::{DefaultTerminal, Frame};

use crate::keys::{RawTerminal, read_key};
use crate::replay::{GameStream, ReplaySettings, autoplay_tick, goto_key, replay_key, status_line};
use crate::schema::{Record, field, int, text, truthy};
use crate::view::{
    EVENT_STYLE, ViewState, battlefield, bf_visible, format_event, group_battlefield, players,
    stack,
};

/// Player panels per grid row (2x2 layout for pods).
const GRID_COLS: usize = 2;
/// Battlefield rows per panel before truncation in 3-4 player pods.
const PANEL_MAX_ROWS: usize = 8;
/// Life-total display thresholds against the 40-life start (CR 903.7).
const LIFE_COMFORTABLE: i64 = 30;
const LIFE_DANGER: i64 = 10;

/// A rich style name as a ratatui style.
fn style(name: &str) -> Style {
    let mut st = Style::default();
    for word in name.split_whitespace() {
        st = match word {
            "bold" => st.add_modifier(Modifier::BOLD),
            "dim" => st.add_modifier(Modifier::DIM),
            "italic" => st.add_modifier(Modifier::ITALIC),
            "strike" => st.add_modifier(Modifier::CROSSED_OUT),
            "cyan" => st.fg(Color::Cyan),
            "blue" => st.fg(Color::Blue),
            "green" => st.fg(Color::Green),
            "green4" => st.fg(Color::Indexed(28)),
            "yellow" => st.fg(Color::Yellow),
            "bright_yellow" => st.fg(Color::LightYellow),
            "gold3" => st.fg(Color::Indexed(178)),
            "red" => st.fg(Color::Red),
            "red3" => st.fg(Color::Indexed(160)),
            "magenta" => st.fg(Color::Magenta),
            "white" => st.fg(Color::White),
            "black" => st.fg(Color::Black),
            "grey42" => st.fg(Color::Indexed(242)),
            _ => st,
        };
    }
    st
}

fn life_style(life: i64) -> Style {
    if life > LIFE_COMFORTABLE {
        style("bold green")
    } else if life > LIFE_DANGER {
        style("bold yellow")
    } else {
        style("bold red")
    }
}

fn span(text: impl Into<String>, st: &str) -> Span<'static> {
    Span::styled(text.into(), style(st))
}

/// One battlefield row: `3x Insect 1/1 (T) [+1/+1:2] >>Bob`.
fn perm_line(count: usize, p: &Record) -> Line<'static> {
    let mut spans = Vec::new();
    if count > 1 {
        spans.push(span(format!("{count}x "), "bold"));
    }
    let tapped = truthy(p, "tapped");
    if truthy(p, "commander") {
        spans.push(span("* ", "gold3"));
    }
    let name_style = if tapped {
        "dim"
    } else if truthy(p, "token") {
        "italic"
    } else {
        ""
    };
    spans.push(span(text(p, "name").to_owned(), name_style));
    if truthy(p, "pt") {
        let dmg = if truthy(p, "damage") {
            format!("({})", int(p, "damage"))
        } else {
            String::new()
        };
        spans.push(span(
            format!(" {}{dmg}", text(p, "pt")),
            if tapped { "dim" } else { "bold" },
        ));
    }
    if tapped {
        spans.push(span(" (T)", "dim"));
    }
    if let Some(pycompat::json::Value::Dict(counters)) = field(p, "counters") {
        for (kind, n) in counters {
            spans.push(span(
                format!(" [{kind}:{}]", mtgcards::jsonval::py_str(n)),
                "cyan",
            ));
        }
    }
    if truthy(p, "attacking") {
        spans.push(span(format!(" >>{}", text(p, "attacking")), "yellow"));
    }
    if let Some(pycompat::json::Value::List(blocking)) = field(p, "blocking")
        && !blocking.is_empty()
    {
        let names: Vec<String> = blocking.iter().map(mtgcards::jsonval::py_str).collect();
        spans.push(span(format!(" ##{}", names.join(",")), "gold3"));
    }
    Line::from(spans)
}

/// Life / zone counts / pools / commander-damage line of a panel.
fn panel_header(pd: &Record) -> Line<'static> {
    let life = int(pd, "life");
    let mut spans = vec![
        Span::styled(format!("{life:>3} "), life_style(life)),
        span(
            format!(
                "H:{} L:{} G:{}",
                int(pd, "hand"),
                int(pd, "library"),
                int(pd, "graveyard")
            ),
            "dim",
        ),
    ];
    if truthy(pd, "poison") {
        spans.push(span(format!(" P:{}", int(pd, "poison")), "bold green4"));
    }
    if truthy(pd, "energy") {
        spans.push(span(format!(" E:{}", int(pd, "energy")), "cyan"));
    }
    if truthy(pd, "mana_pool") {
        spans.push(span(
            format!(" pool:{}", text(pd, "mana_pool")),
            "bold magenta",
        ));
    }
    if let Some(pycompat::json::Value::Dict(dmg)) = field(pd, "cmd_damage")
        && !dmg.is_empty()
    {
        let parts: Vec<String> = dmg
            .iter()
            .map(|(k, v)| {
                format!(
                    "{}:{}",
                    k.chars().take(14).collect::<String>(),
                    mtgcards::jsonval::py_str(v)
                )
            })
            .collect();
        spans.push(span(format!("  cmd<{}", parts.join(" ")), "red"));
    }
    if truthy(pd, "commander_in_command") {
        spans.push(span("  [cmd zone]", "gold3"));
    }
    Line::from(spans)
}

/// Grouped battlefield rows plus a one-line land summary.
fn battlefield_lines(pd: &Record, bf_filter: &str, max_rows: Option<usize>) -> Vec<Line<'static>> {
    let mut lines = Vec::new();
    let mut lands: Vec<(usize, &Record)> = Vec::new();
    let mut rest: Vec<(usize, &Record)> = Vec::new();
    for (count, p) in group_battlefield(battlefield(pd)) {
        if !bf_visible(p, bf_filter) {
            continue;
        }
        let is_land = matches!(field(p, "types"), Some(pycompat::json::Value::List(items)) if items.iter().any(|t| mtgcards::jsonval::py_str(t) == "Land"));
        if is_land { &mut lands } else { &mut rest }.push((count, p));
    }
    // busiest boards first when space is tight (4-player pods)
    match max_rows {
        Some(max) if rest.len() > max => {
            let (shown, hidden) = rest.split_at(max - 1);
            for (count, p) in shown {
                lines.push(perm_line(*count, p));
            }
            let more: usize = hidden.iter().map(|(c, _)| *c).sum();
            lines.push(Line::from(span(format!("... +{more} more"), "dim")));
        }
        _ => {
            for (count, p) in &rest {
                lines.push(perm_line(*count, p));
            }
        }
    }
    if !lands.is_empty() {
        let n: usize = lands.iter().map(|(c, _)| *c).sum();
        let tapped: usize = lands
            .iter()
            .filter(|(_, p)| truthy(p, "tapped"))
            .map(|(c, _)| *c)
            .sum();
        lines.push(Line::from(span(
            format!("{n} lands ({} untapped)", n - tapped),
            "dim",
        )));
    }
    lines
}

/// One player's panel.
fn player_panel(
    frame: &mut Frame<'_>,
    area: Rect,
    pd: &Record,
    active: bool,
    bf_filter: &str,
    max_rows: Option<usize>,
) {
    let mut lines = vec![panel_header(pd)];
    lines.extend(battlefield_lines(pd, bf_filter, max_rows));
    let lost = truthy(pd, "lost");
    let title: String = text(pd, "name").chars().take(28).collect();
    let title = if lost {
        span(title, "strike red")
    } else {
        Span::raw(title)
    };
    if lost {
        lines.push(Line::from(span(
            format!("LOST: {}", text(pd, "lost")),
            "bold red",
        )));
    }
    let border = if active && !lost {
        "bright_yellow"
    } else if lost {
        "red"
    } else {
        "grey42"
    };
    let mut block = Block::bordered().title(title).border_style(style(border));
    if active {
        block = block.title_bottom("ACTIVE");
    }
    frame.render_widget(Paragraph::new(Text::from(lines)).block(block), area);
}

/// Turn/phase/active header, incl. seed and (at the end) the winner.
fn frame_header(view: &ViewState, snap: &Record, bf_filter: &str) -> Line<'static> {
    let mut spans = vec![
        Span::styled(
            format!(" Turn {} ", int(snap, "turn")),
            style("bold white").bg(Color::Indexed(237)),
        ),
        span(format!(" {} ", text(snap, "phase")), "bold cyan"),
        span(format!(" active: {} ", text(snap, "active")), "yellow"),
    ];
    if let Some(seed) = field(&view.meta, "seed")
        && !matches!(seed, pycompat::json::Value::Null)
    {
        let game =
            field(&view.meta, "game").map_or_else(|| "1".to_owned(), mtgcards::jsonval::py_str);
        spans.push(span(
            format!(" seed {} game {game} ", mtgcards::jsonval::py_str(seed)),
            "dim",
        ));
    }
    if let Some(result) = &view.result
        && view.at_end()
    {
        // no spoilers mid-replay
        spans.push(span(
            format!(
                "  WINNER: {} ({}) ",
                text(result, "winner"),
                text(result, "reason")
            ),
            "bold green",
        ));
    }
    if bf_filter != "all" {
        spans.push(span(format!(" [battlefield: {bf_filter}] "), "magenta"));
    }
    Line::from(spans)
}

/// Player grid (2x2 for pods; compact panels when 3-4 players).
fn players_grid(frame: &mut Frame<'_>, area: Rect, snap: &Record, bf_filter: &str) {
    let pds = players(snap);
    let per_row = if pds.len() > 1 { GRID_COLS } else { 1 };
    let max_rows = (pds.len() > GRID_COLS).then_some(PANEL_MAX_ROWS);
    let n_rows = pds.len().div_ceil(per_row).max(1);
    let ratio = |n: usize| Constraint::Ratio(1, u32::try_from(n).unwrap_or(1));
    let rows = Layout::vertical(vec![ratio(n_rows); n_rows]).split(area);
    for (r, chunk) in pds.chunks(per_row).enumerate() {
        let cols = Layout::horizontal(vec![ratio(per_row); per_row]).split(rows[r]);
        for (c, pd) in chunk.iter().enumerate() {
            let active = text(pd, "name") == text(snap, "active");
            player_panel(frame, cols[c], pd, active, bf_filter, max_rows);
        }
    }
}

/// Attack arrows with blocker annotations for the COMBAT panel.
fn combat_lines(pds: &[Record]) -> Vec<Line<'static>> {
    let mut lines = Vec::new();
    for pd in pds {
        for (count, p) in group_battlefield(battlefield(pd)) {
            if !truthy(p, "attacking") {
                continue;
            }
            let n = if count > 1 {
                format!("{count}x ")
            } else {
                String::new()
            };
            let mut blocked = Vec::new();
            for qd in pds {
                for (_, q) in group_battlefield(battlefield(qd)) {
                    let blocks_it = matches!(field(q, "blocking"), Some(pycompat::json::Value::List(items)) if items.iter().any(|b| mtgcards::jsonval::py_str(b) == text(p, "name")));
                    if blocks_it {
                        blocked.push(text(q, "name").to_owned());
                    }
                }
            }
            let mut arrow = format!(
                "{n}{} {} -> {}",
                text(p, "name"),
                text(p, "pt"),
                text(p, "attacking")
            );
            let st = if blocked.is_empty() {
                arrow.push_str("  unblocked");
                "yellow"
            } else {
                let _ = write!(arrow, "  blocked by {}", blocked.join(", "));
                "gold3"
            };
            lines.push(Line::from(span(arrow, st)));
        }
    }
    lines
}

/// STACK and COMBAT panels side by side.
fn stack_combat_row(frame: &mut Frame<'_>, area: Rect, snap: &Record) {
    let cols = Layout::horizontal([Constraint::Ratio(1, 2), Constraint::Ratio(1, 2)]).split(area);
    let items = stack(snap);
    let mut stack_lines: Vec<Line<'static>> = items
        .iter()
        .rev()
        .enumerate()
        .map(|(i, s)| {
            Line::from(span(
                format!("{}. {s}", items.len() - i),
                if i == 0 { "bold blue" } else { "blue" },
            ))
        })
        .collect();
    if stack_lines.is_empty() {
        stack_lines.push(Line::from(span("(empty)", "dim")));
    }
    let mut combat = combat_lines(players(snap));
    if combat.is_empty() {
        combat.push(Line::from(span("(no combat)", "dim")));
    }
    frame.render_widget(
        Paragraph::new(Text::from(stack_lines)).block(
            Block::bordered()
                .title("STACK (top first)")
                .border_style(style("blue")),
        ),
        cols[0],
    );
    frame.render_widget(
        Paragraph::new(Text::from(combat)).block(
            Block::bordered()
                .title("COMBAT")
                .border_style(style("yellow")),
        ),
        cols[1],
    );
}

/// Scrolling, colour-coded event log.
fn log_panel(frame: &mut Frame<'_>, area: Rect, view: &ViewState, log_filter: Option<&str>) {
    let mut lines: Vec<Line<'static>> = view
        .visible_events(12, log_filter)
        .into_iter()
        .map(|rec| {
            let kind = text(rec, "kind");
            let st = EVENT_STYLE
                .iter()
                .find(|(k, _)| *k == kind)
                .map_or("", |(_, s)| *s);
            Line::from(vec![
                span(format!("T{:>2} ", int(rec, "turn")), "dim"),
                span(format_event(rec), st),
            ])
        })
        .collect();
    if lines.is_empty() {
        lines.push(Line::from("..."));
    }
    let title = log_filter.map_or_else(|| "LOG".to_owned(), |f| format!("LOG [{f}]"));
    frame.render_widget(
        Paragraph::new(Text::from(lines))
            .wrap(Wrap { trim: false })
            .block(Block::bordered().title(title).border_style(style("grey42"))),
        area,
    );
}

/// Render one full frame: header, boards, stack/combat, log, status.
pub fn render_frame(
    frame: &mut Frame<'_>,
    view: &ViewState,
    status: &str,
    log_filter: Option<&str>,
    bf_filter: &str,
) {
    let area = frame.area();
    let Some(snap) = view.snapshot() else {
        frame.render_widget(
            Paragraph::new("waiting for first snapshot ...").block(Block::bordered()),
            area,
        );
        return;
    };
    let rows = Layout::vertical([
        Constraint::Length(1),
        Constraint::Min(8),
        Constraint::Length(6),
        Constraint::Length(14),
        Constraint::Length(1),
    ])
    .split(area);
    frame.render_widget(Paragraph::new(frame_header(view, snap, bf_filter)), rows[0]);
    players_grid(frame, rows[1], snap, bf_filter);
    stack_combat_row(frame, rows[2], snap);
    log_panel(frame, rows[3], view, log_filter);
    frame.render_widget(
        Paragraph::new(Span::styled(
            status.to_owned(),
            style("black").bg(Color::Indexed(248)),
        )),
        rows[4],
    );
}

/// Enter the alternate screen with raw input; restore on drop.
pub struct Screen {
    /// the terminal
    pub terminal: DefaultTerminal,
    _raw: RawTerminal,
}

impl Screen {
    /// Take over the terminal.
    ///
    /// # Errors
    ///
    /// The terminal refuses raw mode or the alternate screen.
    pub fn enter() -> std::io::Result<Self> {
        let raw = RawTerminal::enter()?;
        let terminal = ratatui::try_init()?;
        Ok(Screen {
            terminal,
            _raw: raw,
        })
    }
}

impl Drop for Screen {
    fn drop(&mut self) {
        ratatui::restore();
    }
}

/// The interactive replay of one recorded game.
pub fn replay_app(stream: GameStream) {
    let Ok(mut screen) = Screen::enter() else {
        eprintln!("note: no terminal; using the plain step viewer");
        let stdin = std::io::stdin();
        let mut input = stdin.lock();
        crate::replay::plain_replay(stream, &mut input, &mut std::io::stdout());
        return;
    };
    let GameStream {
        meta,
        records,
        result,
    } = stream;
    let mut view = ViewState::new(records, meta);
    view.result = result;
    view.step(1);
    let mut st = ReplaySettings::default();
    let player_names: Vec<String> = view
        .snapshot()
        .map(|snap| {
            players(snap)
                .iter()
                .map(|p| text(p, "name").to_owned())
                .collect()
        })
        .unwrap_or_default();
    loop {
        let status = status_line(&view, &st);
        let draw = screen.terminal.draw(|frame| {
            render_frame(
                frame,
                &view,
                &status,
                st.log_filter.as_deref(),
                &st.bf_filter,
            );
        });
        if draw.is_err() {
            return;
        }
        let timeout = st
            .autoplay
            .then(|| Duration::from_secs_f64(0.35 / st.speed));
        let Some(key) = read_key(timeout) else {
            autoplay_tick(&mut view, &mut st); // autoplay tick
            continue;
        };
        if st.goto.is_some() {
            goto_key(&key, &mut view, &mut st);
            continue;
        }
        if !replay_key(&key, &mut view, &mut st, &player_names) {
            return;
        }
    }
}
