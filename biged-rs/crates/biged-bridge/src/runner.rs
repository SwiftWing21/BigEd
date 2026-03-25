use crate::loader::SkillLoader;
use pyo3::prelude::*;
use pyo3::types::{PyAnyMethods, PyDict};
use std::path::Path;
use tracing::{debug, warn};

pub struct SkillRunner {
    loader: SkillLoader,
}

impl SkillRunner {
    pub fn new(fleet_dir: &Path) -> anyhow::Result<Self> {
        let loader = SkillLoader::new(fleet_dir)?;
        Ok(Self { loader })
    }

    /// Execute a skill's `run(payload, config)` or `run(payload, config, log)` function.
    /// Automatically detects 3-arg skills and provides a Python logger.
    /// Returns the result as a `serde_json::Value`.
    pub fn run_skill(
        &self,
        skill_name: &str,
        payload: &serde_json::Value,
        config: &serde_json::Value,
    ) -> anyhow::Result<serde_json::Value> {
        let skill = self.loader.load(skill_name)?;

        Python::attach(|py| {
            // Convert JSON values to Python dicts via json.loads
            let payload_dict = json_to_pydict(py, payload)?;
            let config_dict = json_to_pydict(py, config)?;

            // Call module.run — pass a logger as 3rd arg when arity >= 3
            let run_fn = skill.module.getattr(py, "run")?;
            let result = if skill.arity >= 3 {
                let logging = py.import("logging")?;
                let logger = logging.call_method1("getLogger", (skill_name,))?;
                run_fn.call1(py, (payload_dict, config_dict, logger))
            } else {
                run_fn.call1(py, (payload_dict, config_dict))
            };

            match result {
                Ok(py_result) => {
                    let bound = py_result.bind(py);
                    let json_val = pyobj_to_json(py, bound)?;
                    debug!("Skill '{}' returned successfully", skill_name);
                    Ok(json_val)
                }
                Err(e) => {
                    warn!("Skill '{}' raised exception: {}", skill_name, e);
                    Err(anyhow::anyhow!("Skill '{}' failed: {}", skill_name, e))
                }
            }
        })
    }

    pub fn loader(&self) -> &SkillLoader {
        &self.loader
    }
}

/// Convert a `serde_json::Value` to a Python dict via `json.loads`.
fn json_to_pydict<'py>(py: Python<'py>, value: &serde_json::Value) -> PyResult<Bound<'py, PyDict>> {
    let json_mod = py.import("json")?;
    let json_str = serde_json::to_string(value).map_err(|e| {
        pyo3::exceptions::PyValueError::new_err(format!("JSON serialization failed: {e}"))
    })?;
    let py_obj = json_mod.call_method1("loads", (json_str,))?;
    py_obj
        .cast_into::<PyDict>()
        .map_err(|e| pyo3::exceptions::PyTypeError::new_err(format!("Expected dict, got: {e}")))
}

/// Convert a Python object to `serde_json::Value` via `json.dumps`.
fn pyobj_to_json(py: Python<'_>, obj: &Bound<'_, PyAny>) -> anyhow::Result<serde_json::Value> {
    let json_mod = py.import("json")?;
    let json_str: String = json_mod.call_method1("dumps", (obj,))?.extract()?;
    let value: serde_json::Value = serde_json::from_str(&json_str)?;
    Ok(value)
}
