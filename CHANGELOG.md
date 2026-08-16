# Changelog

All notable changes to FlowClip will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Nothing yet.

## [1.4.0] - 2026-08-16

### Added

- Added native Windows drag-and-drop upload to both the main window and the
  file-transfer window without adding a drag-and-drop runtime dependency.
- Added Android standard drag-and-drop receiving for text, files, images and
  mixed multi-item content, plus `ACTION_SEND_MULTIPLE` share-target support.
- Added sequential multi-file uploads with one worker and one cancellation
  scope on both Windows and Android.

### Changed

- Android image URIs received from drag-and-drop or a multi-item share are sent
  as ordinary files, while received text remains clipboard content.
- Documented best-effort compatibility and test boundaries for HarmonyOS 4,
  MagicOS, ColorOS and OriginOS transfer-station features.

### Security

- Android external drag-and-share URIs are restricted to `content://` and kept
  under temporary read grants only for the active transfer.

## [1.3.0] - 2026-08-15

### Added

- Added the `files-v1` API and desktop/Android file-transfer interfaces for
  listing, uploading, downloading and deleting ordinary files independently of
  the latest clipboard item.
- Added fixed-length raw binary streaming for file bodies, transfer progress,
  cancellation and end-to-end size/SHA-256 verification without Base64 file
  payloads.
- Added desktop download recovery with temporary files, `Range`, `If-Range`,
  SHA-256 `ETag` values and collision-safe destination names.
- Added an Android HTTP server, backed by NanoHTTPD 2.3.1, with a selectable
  unprivileged port and display of the phone's current usable IPv4 URLs.
- Added persistent desktop and Android file stores with platform-specific
  download locations and configurable per-file limits.

### Changed

- Android's `connectedDevice` foreground service now keeps either automatic
  receive, the phone server, or both running, and restores configured background
  roles after boot or app replacement where Android permits it.
- Desktop file settings now separate the server store directory, client download
  directory, per-file limit and total server storage quota.
- Documented the server and client differences for Range support, interrupted
  download recovery, cancellation cleanup and Android storage lifecycle.
- Hardened Android foreground-service cancellation, background-role reconfiguration,
  Activity recreation state, transfer progress lifecycle guards and duplicate manual-action handling.
- Added packaged desktop import smoke tests, owned draft-first GitHub Releases,
  cancellation cleanup, default-branch tag validation and complete third-party license bundles.
- Pinned every GitHub Action to a verified commit SHA and added a standalone Android
  build/signing guide with external build-directory support.
- Pinned desktop runtime and build transitive dependencies used by native packaging.

### Security

- File endpoints require the existing Bearer token, reject unsupported transfer
  framing, bound metadata and transfer concurrency, and verify completed file
  bytes with SHA-256.
- Documented that both desktop and Android embedded servers are HTTP-only and
  require a trusted LAN, encrypted VPN, or HTTPS tunnel for confidential traffic.
- Disabled Android cloud backup and device transfer for FlowClip configuration,
  indexes and credentials with explicit data-extraction rules.

## [1.2.0] - 2026-08-15

### Added

- Linux desktop support for Wayland (`wl-clipboard`) and X11 (`xclip`), including
  text and image synchronization, XDG configuration, autostart and single-instance
  locking.
- macOS desktop support using Cocoa `NSPasteboard`, menu bar integration,
  LaunchAgent autostart and native application packaging.
- Native Windows, Linux and macOS build jobs plus tagged GitHub Releases, checksums
  and provenance attestations.
- Public contribution, security, conduct, privacy, platform and release-process
  documentation.
- Cross-platform clipboard, startup, configuration and protocol validation tests.

### Changed

- Renamed the shared desktop source directory from `windows/` to `desktop/`.
- Unified desktop, Windows resource and Android versions at 1.2.0.
- Made the desktop User-Agent, fonts, configuration paths, startup integration,
  tray failure behavior and single-instance handling platform-aware.
- Added a controlled PyInstaller spec so macOS bundle metadata uses the release
  version and unsupported build hosts fail explicitly.
- Restricted automated GitHub Releases to strict stable SemVer tags and expanded
  the version gate to every Windows file/product version field.
- Tightened MIME, filename and canonical Base64 validation consistently in Python
  and Android.

### Security

- Linux and macOS desktop configuration directories now use mode `0700`; config
  files and instance locks use mode `0600`.
- The built-in HTTP server no longer exposes Python or stale application version
  details in its `Server` response header.
- Added CodeQL, dependency review, Dependabot and private vulnerability reporting
  guidance.

Release entries before the first public repository tag have not been reconstructed.
