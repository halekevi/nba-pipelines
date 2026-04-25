# PropORACLE Mobile App Structure

This map outlines the relationship between web source files and the native Android bundle.

## Project Roots
- **Mobile Root**: `mobile/`
- **Capacitor Project Config**: `mobile/capacitor.config.js`
- **Mobile NPM Scripts**: `mobile/package.json`

## Web UI Source (The "www" folder)
- **Source for App UI**: `mobile/www/`
- **Payout Logger Source**: `mobile/www/payout_log.html`
- **Shared Templates (Server-side)**: `ui_runner/templates/payout_log.html`

## Android Native Project
- **Android Root**: `mobile/android/`
- **App Module**: `mobile/android/app/`
- **Android Manifest**: `mobile/android/app/src/main/AndroidManifest.xml`
- **MainActivity**: `mobile/android/app/src/main/java/com/proporacle/app/MainActivity.java`

## Bundled Assets (Inside APK)
- **Bundled Web Assets**: `mobile/android/app/src/main/assets/public/`
- **Capacitor Runtime Config**: `mobile/android/app/src/main/assets/capacitor.config.json`
