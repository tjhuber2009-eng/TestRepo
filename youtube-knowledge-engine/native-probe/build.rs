fn main() {
    tauri_build::try_build(
        tauri_build::Attributes::new().app_manifest(
            tauri_build::AppManifest::new().commands(&[
                "desktop_provider_status",
                "desktop_save_provider_settings",
            ]),
        ),
    )
    .expect("failed to build Tauri application metadata");
}
