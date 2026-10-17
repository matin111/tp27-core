# TwinPanel Public Bootstrap

این پوشه تنها مسیر عمومی نصب و بروزرسانی TwinPanel است. سورس خصوصی برنامه و کلیدهای امضای Vendor در این مخزن عمومی قرار نمی‌گیرند.

This directory is the only public installation/update entry point for TwinPanel. Private product source and vendor signing secrets are never stored in this public repository.

## فارسی

### نصب رسمی

```bash
curl -fsSL https://raw.githubusercontent.com/matin111/tp27-core/main/twinpanel/install.sh | sudo bash
```

Bootstrap آدرس سرویس License/Distribution را از `twinpanel/config.json` می‌خواند. تا وقتی سرویس مرکزی منتشر نشده، config بدون آدرس API باقی می‌ماند و Installer قبل از هر تغییر روی سرور با پیام واضح متوقف می‌شود.

بعد از فعال‌شدن سرویس مرکزی، کاربر فقط همین یک دستور را اجرا می‌کند و نیازی به GitHub Token یا دسترسی به مخزن خصوصی ندارد.

اگر License Key وارد نشود، برای همان Installation یک Trial هفت‌روزه درخواست می‌شود. اگر License Key وارد شود، همان Installation مستقیماً به لایسنس پولی متصل می‌شود.

برای تست Vendor قبل از قراردادن دامنه نهایی در config عمومی:

```bash
export TWINPANEL_LICENSE_API_URL="https://YOUR-LICENSE-DOMAIN"
curl -fsSL https://raw.githubusercontent.com/matin111/tp27-core/main/twinpanel/install.sh | sudo -E bash
```

## English

### Official installation

```bash
curl -fsSL https://raw.githubusercontent.com/matin111/tp27-core/main/twinpanel/install.sh | sudo bash
```

The bootstrap reads the License/Distribution service URL from `twinpanel/config.json`. Until the central service is published, the config intentionally contains no API URL and the installer exits before changing the server.

After the vendor service is enabled, customers need only the one command above and never need a GitHub token or access to the private product repository.

Without a paid key, the bootstrap requests the seven-day trial for that installation. A paid key activates the same installation in place.

Vendor testing override before publishing the final license hostname:

```bash
export TWINPANEL_LICENSE_API_URL="https://YOUR-LICENSE-DOMAIN"
curl -fsSL https://raw.githubusercontent.com/matin111/tp27-core/main/twinpanel/install.sh | sudo -E bash
```

## Public release files

- `config.json` — single public non-secret bootstrap configuration
- `releases/stable.json` — single Stable version/update announcement source
- `install.sh` — public installer bootstrap

A release is not offered to installed panels until the Stable manifest is marked installable and the License/Distribution service has the exact validated artifact.

## Security

- No vendor signing private key is stored here.
- No private TwinPanel source is stored here.
- No private-repository GitHub token is embedded in the installer.
- Customer package downloads are short-lived and entitlement-controlled by the vendor API.
- Package SHA256 is verified before extraction.
- The customer receives only the entitlement verification public key.
