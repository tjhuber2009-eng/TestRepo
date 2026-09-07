# Third-party desktop media tools

YouTube Intelligence Desktop can use externally supplied or installer-bundled `yt-dlp` and `FFmpeg` executables. These programs are separate processes and are not included in this source checkpoint.

## yt-dlp

The yt-dlp project source is principally dedicated to the public domain under the Unlicense, but official PyInstaller executables include third-party GPLv3+ code and the combined executable is distributed under GPLv3+. Any installer that redistributes an official executable must preserve the applicable license/notices and corresponding-source obligations. See the yt-dlp project `LICENSE`, `README.md`, and `THIRD_PARTY_LICENSES.txt` for the exact artifact being shipped.

## FFmpeg

FFmpeg is primarily LGPL-2.1-or-later, but builds that enable GPL components are GPL-2.0-or-later. Desktop distributors must know the configuration and license of the exact binary they ship and satisfy the corresponding notice/source requirements. This project intentionally does not silently download or commit an arbitrary FFmpeg binary.

## Build behavior

`npm run desktop:prepare-tools -- --yt-dlp <path> --ffmpeg <path>` copies operator-supplied, reviewed binaries into the Tauri resource directory for the current installer build. If no bundled tool is supplied, the application falls back to `YTDLP_BIN` / `FFMPEG_BIN` or the system `PATH`.
