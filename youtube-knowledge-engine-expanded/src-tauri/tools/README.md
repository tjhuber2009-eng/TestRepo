# Optional bundled desktop tools

Desktop installers may place `yt-dlp`/`yt-dlp.exe` and `ffmpeg`/`ffmpeg.exe` in this resource directory before the Tauri build. The application prefers these private bundled tools and otherwise falls back to PATH.

Use `npm run desktop:prepare-tools -- --yt-dlp <path> --ffmpeg <path>` to copy operator-supplied, license-reviewed binaries into this directory for the current build. Third-party binaries are intentionally not committed to the source release.
