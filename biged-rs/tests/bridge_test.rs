use biged_bridge::loader::SkillLoader;
use std::path::PathBuf;

fn fleet_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .join("fleet")
}

#[test]
fn test_loader_initializes() {
    let fd = fleet_dir();
    if !fd.join("skills").exists() {
        return; // Skip if fleet dir not available
    }
    let loader = SkillLoader::new(&fd).expect("loader should initialize");
    assert_eq!(loader.cached_count(), 0);
}

#[test]
fn test_loader_imports_skill() {
    let fd = fleet_dir();
    if !fd.join("skills").exists() {
        return;
    }
    let loader = SkillLoader::new(&fd).expect("loader should initialize");

    // autoresearch_analyze is a simple skill with no network deps
    let result = loader.load("autoresearch_analyze");
    assert!(
        result.is_ok(),
        "should load autoresearch_analyze: {:?}",
        result.err()
    );
    assert_eq!(loader.cached_count(), 1);

    // Loading again should use cache
    let result2 = loader.load("autoresearch_analyze");
    assert!(result2.is_ok());
    assert_eq!(loader.cached_count(), 1, "should still be 1 — cached");
}

#[test]
fn test_loader_missing_skill() {
    let fd = fleet_dir();
    if !fd.join("skills").exists() {
        return;
    }
    let loader = SkillLoader::new(&fd).expect("loader should initialize");
    let result = loader.load("nonexistent_skill_xyz");
    assert!(result.is_err());
}
