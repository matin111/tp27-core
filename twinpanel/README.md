# TwinPanel Public Bootstrap

This directory contains only the public bootstrap and non-sensitive release metadata for TwinPanel. The customer-installable application source remains private and is delivered only through the license/distribution service.

## فارسی

نصب رسمی:

```bash
curl -fsSL https://raw.githubusercontent.com/matin111/tp27-core/main/twinpanel/install.sh | sudo bash
```

قبل از عمومی‌شدن سرویس لایسنس، Vendor باید آدرس API را تنظیم کند:

```bash
export TWINPANEL_LICENSE_API="https://YOUR-LICENSE-DOMAIN"
curl -fsSL https://raw.githubusercontent.com/matin111/tp27-core/main/twinpanel/install.sh | sudo -E bash
```

اگر License Key وارد نشود، Bootstrap درخواست Trial هفت‌روزه می‌دهد. اگر License Key وارد شود، همان Installation به License پولی متصل می‌شود.

## English

Official install:

```bash
curl -fsSL https://raw.githubusercontent.com/matin111/tp27-core/main/twinpanel/install.sh | sudo bash
```

Until the vendor license service has its final public hostname, configure it explicitly:

```bash
export TWINPANEL_LICENSE_API="https://YOUR-LICENSE-DOMAIN"
curl -fsSL https://raw.githubusercontent.com/matin111/tp27-core/main/twinpanel/install.sh | sudo -E bash
```

Without a paid key the bootstrap requests the seven-day trial. A paid key upgrades the same installation without reinstalling.

## Security

- No vendor signing key is stored here.
- No private TwinPanel source is stored here.
- No GitHub private-repository token is embedded in the installer.
- Actual release downloads are short-lived and entitlement-controlled by the vendor API.
