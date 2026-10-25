# TP27 Domain TLS

## معماری

حالت Domain TLS برای کنترلر TP27 از HAProxy روی `:8787` استفاده می‌کند و Uvicorn را فقط روی `127.0.0.1:18787` نگه می‌دارد.

```text
Exit Agent -> https://controller.example:8787 -> HAProxy -> http://127.0.0.1:18787
```

Certificate اصلی از Let's Encrypt برای DNS name صادر می‌شود. فایل PEM مصرفی HAProxy در این مسیر ساخته می‌شود:

```text
/etc/haproxy/tp27-certs/domain.pem
```

Config runtime:

```text
/etc/tp27/domain-ssl.env
```

## ACME egress

اگر مسیر مستقیم Controller به Let's Encrypt قابل اعتماد نباشد، `tp27-acme-ssh-forward.service` فقط API ACME را از یک Relay خارج عبور می‌دهد. Relay باید با `setup-acme-relay.sh` و user محدود `tp27-acme` آماده شود. Authorized key به IP کنترلر و `permitopen=acme-v02.api.letsencrypt.org:443` محدود می‌شود و sudo دریافت نمی‌کند.

## Renewal

`tp27-domain-cert-renew.timer` روزانه اجرا می‌شود اما تا زمانی که بیش از ۳۰ روز از اعتبار Certificate باقی مانده باشد درخواست صدور جدید نمی‌دهد.

زمان Renewal واقعی:

1. Lock سراسری گرفته می‌شود.
2. سلامت SSH ACME egress بررسی می‌شود.
3. TCP/80 فقط موقت برای HTTP-01 باز می‌شود.
4. `tp27-plain-control.service` فقط در زمان Challenge متوقف می‌شود.
5. Certificate جدید صادر و SAN/اعتبار آن بررسی می‌شود.
6. PEM مخصوص HAProxy به‌صورت atomic بازسازی می‌شود.
7. Config HAProxy validate می‌شود.
8. HAProxy با `reload` بدون Restart پنل reload می‌شود.
9. Strict HTTPS health check روی Domain انجام می‌شود.
10. Firewall، `/etc/hosts` و Plain Control در تمام مسیرهای موفق/خطا Restore می‌شوند.

هیچ Tunnel یا Data Plane در این Flow تغییر نمی‌کند.

## فعال‌سازی Domain-only

پس از انتقال تمام Agentها به Domain و تأیید Poll/Heartbeat:

```bash
TP27_ACME_RELAY_HOST=<relay-ip> \
TP27_ACME_RELAY_USER=tp27-acme \
TP27_ACME_SSH_KEY=<private-key-path> \
bash enable-domain-ssl.sh controller.example <controller-ip> --domain-only
```

این مرحله Timerهای قدیمی IP certificate را Disable می‌کند و deploy hook قدیمی که Panel را Restart می‌کرد به Backup منتقل می‌کند.

## Rollback

`enable-domain-ssl.sh` قبل از تغییر HAProxy در `/etc/tp27/backups/domain-ssl-*` Backup می‌سازد. اگر strict Domain health بعد از تغییر Fail شود، HAProxy config قبلی Restore و reload می‌شود.

برای Emergency rollback به اسکریپت IP SSL، Domain mode به‌صورت پیش‌فرض Downgrade را رد می‌کند. فقط در شرایط اضطراری و آگاهانه می‌توان `TP27_ALLOW_IP_SSL_DOWNGRADE=1` را استفاده کرد.
