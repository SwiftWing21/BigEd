# BigEd CC — Installer Build Guide

## Windows (MSI via WiX)
```bash
cargo build --release
wix build install/windows.wxs -d CargoTargetDir=target
```

## Linux (.deb)
```bash
cargo install cargo-deb
cargo deb
```

## macOS (.dmg)
```bash
cargo build --release
hdiutil create -volname "BigEd CC" -srcfolder target/release/biged -ov BigEdCC.dmg
```
