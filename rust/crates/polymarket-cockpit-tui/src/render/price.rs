use crate::state::AppState;

pub fn price_lines(app: &AppState) -> Vec<String> {
    let Some(monitor) = &app.runtime_monitor else {
        return Vec::new();
    };

    ["BTC/USD", "ETH/USD"]
        .into_iter()
        .filter_map(|symbol| {
            monitor
                .price_rows
                .iter()
                .find(|row| row.symbol == symbol)
                .and_then(|row| row.price.as_deref())
                .map(|price| format!("{symbol} ${}", format_usd_price(price)))
        })
        .collect()
}

fn format_usd_price(raw: &str) -> String {
    let Ok(value) = raw.parse::<f64>() else {
        return raw.to_string();
    };

    add_thousands_separators(&format!("{value:.2}"))
}

fn add_thousands_separators(value: &str) -> String {
    let (whole, fraction) = value.split_once('.').unwrap_or((value, ""));
    let (sign, digits) = whole
        .strip_prefix('-')
        .map_or(("", whole), |rest| ("-", rest));
    let mut grouped = String::new();

    for (index, character) in digits.chars().rev().enumerate() {
        if index > 0 && index % 3 == 0 {
            grouped.push(',');
        }
        grouped.push(character);
    }

    let whole = grouped.chars().rev().collect::<String>();
    if fraction.is_empty() {
        format!("{sign}{whole}")
    } else {
        format!("{sign}{whole}.{fraction}")
    }
}
