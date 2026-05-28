#include "probability_core/probability_core.hpp"

#include <algorithm>

namespace probability_core {

DecisionOutput price_binary(const DecisionInput& input) {
    if (input.seconds_to_expiry <= 0.0 || input.annualized_volatility <= 0.0) {
        return {};
    }

    const double rough_score = input.distance_to_threshold /
                               std::max(input.annualized_volatility, 1e-9);
    const double clamped = std::clamp(0.5 + rough_score, 0.0, 1.0);

    return DecisionOutput{
        .p_finish = clamped,
        .p_no_touch = clamped,
    };
}

}  // namespace probability_core
