use ratatui::{
    Frame,
    layout::Rect,
    style::{Color, Style},
    widgets::{Block, Paragraph},
};
use unicode_width::UnicodeWidthStr;

pub fn render(frame: &mut Frame<'_>, area: Rect) {
    let help = "Tab/Right: next  Shift-Tab/Left: previous  Up/Down: select  q/Esc: quit";
    let display = if help.width() > area.width as usize {
        "Tab/Right: next  q/Esc: quit"
    } else {
        help
    };

    let footer = Paragraph::new(display)
        .style(Style::default().fg(Color::DarkGray))
        .block(Block::bordered().title("Read-only"));

    frame.render_widget(footer, area);
}
