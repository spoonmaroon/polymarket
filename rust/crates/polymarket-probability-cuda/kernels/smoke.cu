extern "C" __global__ void add_one(const double *input, double *output, unsigned long long len) {
    unsigned long long idx = blockIdx.x * blockDim.x + threadIdx.x;
    if (idx < len) {
        output[idx] = input[idx] + 1.0;
    }
}
