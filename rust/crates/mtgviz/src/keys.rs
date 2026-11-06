//! Terminal keyboard input (the Python `keys.py`, on crossterm).
//!
//! [`read_key`] returns single characters, or the named keys for Enter,
//! Escape and the arrows; `None` on timeout.

use std::io::IsTerminal as _;
use std::time::Duration;

use crossterm::event::{self, Event, KeyCode, KeyEventKind, KeyModifiers};

use crate::replay::Key;

/// Whether stdin is an interactive terminal.
#[must_use]
pub fn stdin_is_terminal() -> bool {
    std::io::stdin().is_terminal()
}

/// Put the terminal into raw mode for the duration of the returned guard.
pub struct RawTerminal;

impl RawTerminal {
    /// Enable raw mode.
    ///
    /// # Errors
    ///
    /// The terminal refuses.
    pub fn enter() -> std::io::Result<Self> {
        crossterm::terminal::enable_raw_mode()?;
        Ok(RawTerminal)
    }
}

impl Drop for RawTerminal {
    fn drop(&mut self) {
        let _ = crossterm::terminal::disable_raw_mode();
    }
}

/// One keypress; `None` on timeout (`None` timeout = wait forever).
#[must_use]
pub fn read_key(timeout: Option<Duration>) -> Option<Key> {
    loop {
        if let Some(limit) = timeout
            && !event::poll(limit).unwrap_or(false)
        {
            return None;
        }
        let Ok(Event::Key(key)) = event::read() else {
            // a resize or mouse event: keep waiting (within the same
            // timeout budget, like the Python select loop)
            if timeout.is_some() {
                return None;
            }
            continue;
        };
        if key.kind != KeyEventKind::Press {
            continue;
        }
        return Some(match key.code {
            KeyCode::Char('c') if key.modifiers.contains(KeyModifiers::CONTROL) => Key::Interrupt,
            KeyCode::Char(c) => Key::Char(c),
            KeyCode::Enter => Key::Enter,
            KeyCode::Esc => Key::Esc,
            KeyCode::Left => Key::Left,
            KeyCode::Right => Key::Right,
            KeyCode::Up => Key::Up,
            KeyCode::Down => Key::Down,
            _ => continue,
        });
    }
}
