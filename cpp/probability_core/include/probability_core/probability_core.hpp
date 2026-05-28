#pragma once

namespace probability_core {

struct DecisionInput {
    double distance_to_threshold = 0.0;
    double annualized_volatility = 0.0;
    double seconds_to_expiry = 0.0;
};

struct DecisionOutput {
    double p_finish = 0.0;
    double p_no_touch = 0.0;
};

DecisionOutput price_binary(const DecisionInput& input);

}  // namespace probability_core
