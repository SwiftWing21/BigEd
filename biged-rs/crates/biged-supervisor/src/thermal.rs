use crate::events::{EventSender, FleetEvent, ThermalAction};
use biged_core::config::FleetConfig;
use biged_core::types::HwState;
use std::path::PathBuf;
use std::sync::Arc;
use tokio::sync::RwLock;
use tracing::{debug, info, warn};

pub struct ThermalMonitor {
    config: Arc<RwLock<FleetConfig>>,
    events: EventSender,
    fleet_dir: PathBuf,
    last_gpu_temp: f32,
    #[allow(dead_code)] // Phase 2: cooldown window tracking
    cooldown_until: Option<std::time::Instant>,
}

impl ThermalMonitor {
    pub fn new(config: Arc<RwLock<FleetConfig>>, events: EventSender, fleet_dir: PathBuf) -> Self {
        Self {
            config,
            events,
            fleet_dir,
            last_gpu_temp: 0.0,
            cooldown_until: None,
        }
    }

    /// Main thermal monitoring loop — runs as a tokio task.
    pub async fn run(&mut self) {
        info!("Thermal monitor started");
        let mut interval = tokio::time::interval(std::time::Duration::from_secs(5));

        loop {
            interval.tick().await;

            match self.poll_once().await {
                Ok(state) => {
                    self.write_hw_state(&state).await;
                    self.check_thresholds(&state).await;
                }
                Err(e) => {
                    debug!("Thermal poll failed: {}", e);
                }
            }
        }
    }

    async fn poll_once(&mut self) -> anyhow::Result<HwState> {
        let (gpu_temp, gpu_vram_used, gpu_vram_total, gpu_power) = self.read_gpu()?;
        let cpu_temp = self.read_cpu_temp();
        let (ram_used, ram_total) = self.read_ram();
        let loaded_models = self.read_ollama_models().await;
        let config = self.config.read().await;

        self.last_gpu_temp = gpu_temp;

        Ok(HwState {
            status: "ready".into(),
            gpu_temp_c: gpu_temp,
            gpu_vram_used_gb: gpu_vram_used,
            gpu_vram_total_gb: gpu_vram_total,
            gpu_power_w: gpu_power,
            cpu_temp_c: cpu_temp,
            ram_used_gb: ram_used,
            ram_total_gb: ram_total,
            loaded_models,
            thermal_state: if gpu_temp > config.thermal.gpu_target_c as f32 {
                "throttled".into()
            } else {
                "ready".into()
            },
            current_model: config.models.conductor_model.clone(),
        })
    }

    fn read_gpu(&self) -> anyhow::Result<(f32, f64, f64, f32)> {
        #[cfg(feature = "nvidia")]
        {
            use nvml_wrapper::Nvml;
            match Nvml::init() {
                Ok(nvml) => match nvml.device_by_index(0) {
                    Ok(device) => {
                        let temp = device
                            .temperature(
                                nvml_wrapper::enum_wrappers::device::TemperatureSensor::Gpu,
                            )
                            .unwrap_or(0) as f32;
                        let mem = device.memory_info();
                        let (vram_used, vram_total) = match mem {
                            Ok(m) => (
                                m.used as f64 / 1_073_741_824.0,
                                m.total as f64 / 1_073_741_824.0,
                            ),
                            Err(_) => (0.0, 0.0),
                        };
                        let power = device.power_usage().unwrap_or(0) as f32 / 1000.0;
                        Ok((temp, vram_used, vram_total, power))
                    }
                    Err(_) => Ok((0.0, 0.0, 0.0, 0.0)),
                },
                Err(_) => Ok((0.0, 0.0, 0.0, 0.0)),
            }
        }
        #[cfg(not(feature = "nvidia"))]
        {
            Ok((0.0, 0.0, 0.0, 0.0))
        }
    }

    fn read_cpu_temp(&self) -> f32 {
        // Platform-specific CPU temp reading — placeholder
        0.0
    }

    fn read_ram(&self) -> (f64, f64) {
        // Platform-specific RAM reading — placeholder
        (0.0, 0.0)
    }

    async fn read_ollama_models(&self) -> Vec<String> {
        match reqwest::Client::new()
            .get("http://localhost:11434/api/ps")
            .timeout(std::time::Duration::from_secs(3))
            .send()
            .await
        {
            Ok(resp) => {
                if let Ok(body) = resp.json::<serde_json::Value>().await {
                    body.get("models")
                        .and_then(|m| m.as_array())
                        .map(|arr| {
                            arr.iter()
                                .filter_map(|m| m.get("name").and_then(|n| n.as_str()))
                                .map(String::from)
                                .collect()
                        })
                        .unwrap_or_default()
                } else {
                    vec![]
                }
            }
            Err(_) => vec![],
        }
    }

    async fn write_hw_state(&self, state: &HwState) {
        let path = self.fleet_dir.join("hw_state.json");
        let tmp = self.fleet_dir.join(".hw_state.tmp");
        if let Ok(json) = serde_json::to_string_pretty(state) {
            if let Ok(()) = tokio::fs::write(&tmp, &json).await {
                let _ = tokio::fs::rename(&tmp, &path).await;
            }
        }
    }

    async fn check_thresholds(&mut self, state: &HwState) {
        let config = self.config.read().await;
        let target = config.thermal.gpu_target_c as f32;

        if state.gpu_temp_c > target + 10.0 {
            warn!("GPU temp {}C exceeds emergency threshold", state.gpu_temp_c);
            let _ = self.events.send(FleetEvent::ThermalAlert {
                gpu_temp: state.gpu_temp_c,
                action: ThermalAction::EmergencyStop,
            });
        } else if state.gpu_temp_c > target {
            debug!(
                "GPU temp {}C above target {}C — throttling",
                state.gpu_temp_c, target
            );
            let _ = self.events.send(FleetEvent::ThermalAlert {
                gpu_temp: state.gpu_temp_c,
                action: ThermalAction::ThrottleDown,
            });
        }
    }
}
