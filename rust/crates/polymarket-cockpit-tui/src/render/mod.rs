pub mod footer;
pub mod header;
pub mod live;
pub mod logs;
pub mod market;
pub mod orderbook;
pub mod outcomes;
pub mod price;
pub mod price_path;
pub mod probability;
pub mod systems;
pub mod volatility;

use ratatui::Frame;

use crate::{
    layout,
    state::{AppState, MainTab},
};

pub fn render(frame: &mut Frame<'_>, app: &AppState) {
    let shell = layout::cockpit(frame.area());
    let body = layout::body(shell.body);

    header::render(frame, shell.header, app);
    match app.active_tab {
        MainTab::Live => {
            live::render(frame, body.primary, app);
            systems::render(frame, body.secondary, app);
        }
        MainTab::Systems => {
            systems::render(frame, body.primary, app);
            live::render(frame, body.secondary, app);
        }
        MainTab::Market => {
            market::render(frame, body.primary, app);
            price_path::render(frame, body.secondary, app);
        }
        MainTab::Probability => {
            probability::render(frame, body.primary, app);
            systems::render(frame, body.secondary, app);
        }
        MainTab::Volatility => {
            volatility::render(frame, body.primary, app);
            systems::render(frame, body.secondary, app);
        }
        MainTab::Outcomes => {
            outcomes::render(frame, body.primary, app);
            systems::render(frame, body.secondary, app);
        }
        MainTab::Logs => {
            logs::render(frame, body.primary, app);
            systems::render(frame, body.secondary, app);
        }
    }
    systems::render(frame, body.systems, app);
    logs::render(frame, body.logs, app);
    footer::render(frame, shell.footer);
}
