struct SimulationInput {
    double settlement_price;
    double threshold;
    double per_step_sigma;
    unsigned long long seed;
    unsigned long long path_count;
    unsigned int steps;
    unsigned int operator_code;
};

__device__ unsigned long long mix64(unsigned long long value) {
    value += 0x9E3779B97F4A7C15ULL;
    value = (value ^ (value >> 30)) * 0xBF58476D1CE4E5B9ULL;
    value = (value ^ (value >> 27)) * 0x94D049BB133111EBULL;
    return value ^ (value >> 31);
}

__device__ double uniform_open01(unsigned long long *state) {
    *state = mix64(*state);
    unsigned long long bits = (*state >> 11) | 1ULL;
    return (double)bits * (1.0 / 9007199254740992.0);
}

__device__ double standard_normal(unsigned long long *state) {
    double u1 = uniform_open01(state);
    double u2 = uniform_open01(state);
    return sqrt(-2.0 * log(u1)) * cos(6.28318530717958647692 * u2);
}

__device__ bool satisfies(double price, double threshold, unsigned int operator_code) {
    if (operator_code == 0U) {
        return price > threshold;
    }
    if (operator_code == 1U) {
        return price >= threshold;
    }
    if (operator_code == 2U) {
        return price < threshold;
    }
    return price <= threshold;
}

extern "C" __global__ void simulate_monte_carlo(SimulationInput input, unsigned long long *counts) {
    unsigned long long path_index = blockIdx.x * blockDim.x + threadIdx.x;
    if (path_index >= input.path_count) {
        return;
    }

    unsigned long long state = input.seed ^ (path_index * 0x9E3779B97F4A7C15ULL);
    double log_price = log(input.settlement_price);
    double terminal_price = input.settlement_price;
    bool no_touch = satisfies(terminal_price, input.threshold, input.operator_code);

    for (unsigned int step = 0; step < input.steps; ++step) {
        double next_log_price = log_price + standard_normal(&state) * input.per_step_sigma;
        if (next_log_price != log_price) {
            log_price = next_log_price;
            terminal_price = exp(log_price);
        }
        if (!isfinite(terminal_price) || terminal_price <= 0.0) {
            atomicAdd(&counts[2], 1ULL);
            return;
        }
        no_touch = no_touch && satisfies(terminal_price, input.threshold, input.operator_code);
    }

    if (satisfies(terminal_price, input.threshold, input.operator_code)) {
        atomicAdd(&counts[0], 1ULL);
    }
    if (no_touch) {
        atomicAdd(&counts[1], 1ULL);
    }
}
