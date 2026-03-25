use wasm_bindgen::prelude::*;

#[wasm_bindgen(start)]
pub fn start() -> Result<(), JsValue> {
    // Better panic messages in the browser console.
    console_error_panic_hook::set_once();

    // eframe web entry point.
    let web_options = eframe::WebOptions::default();
    wasm_bindgen_futures::spawn_local(async {
        let _ = eframe::WebRunner::new()
            .start(
                "biged-canvas",
                web_options,
                Box::new(|cc| Ok(Box::new(biged_gui::create_app(cc, "")))),
            )
            .await;
    });

    Ok(())
}
