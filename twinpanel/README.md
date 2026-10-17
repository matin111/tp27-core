# TwinPanel Public Bootstrap

This directory contains only the public bootstrap and non-sensitive release metadata for TwinPanel. The private TwinPanel application archive is delivered only through the license/distribution service.

## فارسی

### نصب رسمی

```bash
curl -fsSL https://raw.githubusercontent.com/matin111/tp27-core/main/twinpanel/install.sh | sudo bash
```

Bootstrap آدرس سرویس لایسنس را از `twinpanel/config.json` می‌خواند. پس از انتشار سرویس مرکزی، کاربر فقط همین یک دستور را اجرا می‌کند و نیازی به GitHub Token یا دسترسی به مخزن خصوصی ندارد.

اگر License Key وارد نشود، برای همان Installation یک Trial هفت‌روزه درخواست می‌شود. اگر License Key وارد شود، همان Installation به لایسنس پولی متصل می‌شود.

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

The bootstrap reads the license service URL from `twinpanel/config.json`. After the vendor service is published, customers need only the one command above and never need a GitHub token or access to the private product repository.

Without a paid key, the bootstrap requests the seven-day trial for that installation. A paid key activates/upgrades the same installation in place.

Vendor testing override before the final public license hostname is placed in config:

```bash
export TWINPANEL_LICENSE_API_URL="https://YOUR-LICENSE-DOMAIN"
curl -fsSL https://raw.githubusercontent.com/matin111/tp27-core/main/twinpanel/install.sh | sudo -E bash
```

## Release metadata

- `config.json` — public non-secret bootstrap configuration
- `releases/stable.json` — update/version announcement source
- `release.json` — compatibility/candidate metadata

A release is not offered to installed panels until its manifest is marked installable by the release process and the license/distribution service has the matching validated artifact.

## Security

- No vendor signing private key is stored here.
- No private TwinPanel source is stored here.
- No GitHub private-repository token is embedded in the installer.
- Release downloads are short-lived and entitlement-controlled by the vendor API.
- The package SHA256 is verified before installation.
