#!/bin/zsh
set -euo pipefail

cd "$(dirname "$0")"

APP_NAME='TextToMp3Dock'
APP_DIR="$PWD/${APP_NAME}.app"
ICONSET_DIR="/private/tmp/${APP_NAME}.iconset"
RES_DIR="$APP_DIR/Contents/Resources"
MACOS_DIR="$APP_DIR/Contents/MacOS"

rm -rf "$APP_DIR" "$ICONSET_DIR"
mkdir -p "$RES_DIR" "$MACOS_DIR" "$ICONSET_DIR"

for size in 16 32 64 128 256 512; do
  src="$PWD/assets/images/logo.png"
  dst="$ICONSET_DIR/icon_${size}x${size}.png"
  sips -z "$size" "$size" "$src" --out "$dst" >/dev/null
  if [ "$size" -le 256 ]; then
    dst2="$ICONSET_DIR/icon_${size}x${size}@2x.png"
    sips -z $((size * 2)) $((size * 2)) "$src" --out "$dst2" >/dev/null
  fi
done

iconutil -c icns "$ICONSET_DIR" -o "$RES_DIR/AppIcon.icns"

cat > "$MACOS_DIR/$APP_NAME" <<'EOF'
#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")/../../../"
if [ ! -x ".venv/bin/python" ]; then
  if ! command -v python3.11 >/dev/null 2>&1; then
    osascript -e 'display alert "Thiếu Python 3.11" message "Hãy cài Python 3.11 trước khi chạy app."'
    exit 1
  fi
  python3.11 -m venv .venv
fi
_venv_python=".venv/bin/python"
_req_hash_file=".venv/.requirements.sha256"
_current_req_hash="$(shasum -a 256 requirements.txt | awk '{print $1}')"
_stored_req_hash=""
if [ -f "$_req_hash_file" ]; then
  _stored_req_hash="$(cat "$_req_hash_file")"
fi
if [ "$_current_req_hash" != "$_stored_req_hash" ]; then
  "$_venv_python" -m pip install -r requirements.txt >/tmp/texttomp3m4a-dock-build.log 2>&1
  printf '%s\n' "$_current_req_hash" > "$_req_hash_file"
fi
exec "$_venv_python" app.pyw
EOF

chmod +x "$MACOS_DIR/$APP_NAME"

cat > "$APP_DIR/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>CFBundleExecutable</key>
	<string>$APP_NAME</string>
	<key>CFBundleIconFile</key>
	<string>AppIcon</string>
	<key>CFBundleIdentifier</key>
	<string>com.vungocnguyenanh.texttomp3dock</string>
	<key>CFBundleName</key>
	<string>$APP_NAME</string>
	<key>CFBundleDisplayName</key>
	<string>$APP_NAME</string>
	<key>CFBundlePackageType</key>
	<string>APPL</string>
	<key>CFBundleShortVersionString</key>
	<string>1.0</string>
	<key>CFBundleVersion</key>
	<string>1</string>
	<key>LSBackgroundOnly</key>
	<false/>
</dict>
</plist>
EOF

/usr/bin/SetFile -a C "$APP_DIR" || true

echo "$APP_DIR"
