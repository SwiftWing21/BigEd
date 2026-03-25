use dashmap::DashMap;
use pyo3::prelude::*;
use pyo3::types::PyAnyMethods;
use std::path::Path;
use std::sync::Arc;
use tracing::{debug, info};

/// A cached skill module together with its run() arity (2 or 3).
pub struct LoadedSkill {
    pub module: Py<PyAny>,
    pub arity: usize,
}

impl LoadedSkill {
    fn clone_ref(&self, py: Python<'_>) -> Self {
        Self {
            module: self.module.clone_ref(py),
            arity: self.arity,
        }
    }
}

/// Caches imported Python skill modules for reuse.
pub struct SkillLoader {
    cache: Arc<DashMap<String, LoadedSkill>>,
}

impl SkillLoader {
    /// Initialize the skill loader. Sets up Python sys.path to include
    /// the fleet directory and fleet/skills directory.
    pub fn new(fleet_dir: &Path) -> anyhow::Result<Self> {
        Python::attach(|py| -> anyhow::Result<()> {
            let sys = py.import("sys")?;
            let path = sys.getattr("path")?;

            // Add fleet/ to sys.path so `import db` works inside skills
            let fleet_str = fleet_dir.to_string_lossy().to_string();
            path.call_method1("insert", (0, &fleet_str))?;

            // Add fleet/skills/ so direct imports of skill submodules work
            let skills_str = fleet_dir.join("skills").to_string_lossy().to_string();
            path.call_method1("insert", (0, &skills_str))?;

            info!("Python sys.path configured: fleet={}", fleet_str);
            Ok(())
        })?;

        Ok(Self {
            cache: Arc::new(DashMap::new()),
        })
    }

    /// Load a skill module by name. Returns a cached `LoadedSkill` if available.
    pub fn load(&self, skill_name: &str) -> anyhow::Result<LoadedSkill> {
        // Check cache first — clone_ref requires the GIL
        if self.cache.contains_key(skill_name) {
            let cloned =
                Python::attach(|py| self.cache.get(skill_name).map(|r| r.clone_ref(py)));
            if let Some(skill) = cloned {
                debug!("Cache hit for skill: {}", skill_name);
                return Ok(skill);
            }
        }

        // Import the module via importlib and inspect run() arity
        let loaded = Python::attach(|py| -> anyhow::Result<LoadedSkill> {
            let importlib = py.import("importlib")?;
            let module_name = format!("skills.{}", skill_name);
            let module = importlib
                .call_method1("import_module", (module_name.as_str(),))
                .map_err(|e| anyhow::anyhow!("Failed to import skill '{}': {}", skill_name, e))?;

            // Verify the skill contract: must have a run() callable
            if !module.hasattr("run")? {
                anyhow::bail!("Skill '{}' has no run() function", skill_name);
            }

            // Inspect the run() function's arity via inspect.signature
            let run_fn = module.getattr("run")?;
            let inspect = py.import("inspect")?;
            let sig = inspect.call_method1("signature", (run_fn,))?;
            let params = sig.getattr("parameters")?;
            let param_count = params.call_method0("__len__")?.extract::<usize>()?;

            debug!(
                "Loaded skill: {} (arity={})",
                skill_name, param_count
            );

            Ok(LoadedSkill {
                module: module.into_any().unbind(),
                arity: param_count,
            })
        })?;

        // Store in cache and return a clone for the caller
        let ret = Python::attach(|py| loaded.clone_ref(py));
        self.cache.insert(skill_name.to_string(), loaded);
        Ok(ret)
    }

    /// Number of cached skill modules.
    pub fn cached_count(&self) -> usize {
        self.cache.len()
    }
}
