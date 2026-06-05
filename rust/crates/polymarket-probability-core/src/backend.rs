use anyhow::Result;

use crate::schema::{ProbabilityInput, SimulationConfig, SimulationRun};

pub trait SimulationBackend {
    fn run(&self, input: &ProbabilityInput, config: &SimulationConfig) -> Result<SimulationRun>;
}
