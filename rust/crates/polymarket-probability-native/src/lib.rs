use std::fmt::Display;

use polymarket_probability_core::backend::SimulationBackend;
use polymarket_probability_core::cpu::CpuRayonBackend;
use polymarket_probability_core::schema::{ProbabilityInput, SimulationConfig};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

#[pyfunction]
fn run_cpu_json(input_json: &str, config_json: &str) -> PyResult<String> {
    let input: ProbabilityInput = serde_json::from_str(input_json).map_err(to_value_error)?;
    let config: SimulationConfig = serde_json::from_str(config_json).map_err(to_value_error)?;
    let backend = CpuRayonBackend;
    let run = backend.run(&input, &config).map_err(to_value_error)?;
    serde_json::to_string(&run).map_err(to_value_error)
}

#[pymodule]
fn polymarket_probability_native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(run_cpu_json, m)?)?;
    Ok(())
}

fn to_value_error(error: impl Display) -> PyErr {
    PyValueError::new_err(error.to_string())
}
