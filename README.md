# TP27 Core

TP27 یک کنترلر مستقل برای ساخت و مدیریت چند Tunnel بین **یک سرور ایران** و **چند سرور خارج** است. پروژه از TwinPanel مستقل است و سرویس‌های VPN اصلی سرور خارج را Restart یا حذف نمی‌کند.

## نصب سریع — فقط یک لینک

لینک نصب در همه سرورها یکی است:

```text
https://raw.githubusercontent.com/matin111/tp27-core/main/install.sh
```

### 1) نصب سرور ایران + پنل

روی سرور ایران با `root` اجرا کنید:

```bash
curl -fsSL https://raw.githubusercontent.com/matin111/tp27-core/main/install.sh | bash -s -- iran
```

Installer به‌صورت خودکار Core، Panel، سرویس‌های systemd، Port Mapping runtime و کنترل Agent را نصب/Upgrade می‌کند. در پایان آدرس پنل و یک دستور آماده برای نصب Exit نمایش داده می‌شود.

Credential پنل در این فایل نگهداری می‌شود:

```text
/root/tp27-panel-credentials.txt
```

پنل به‌صورت پیش‌فرض روی این آدرس در دسترس است:

```text
https://IRAN_IP:8787
```

### 2) نصب هر سرور خارج

دستور آماده‌ای که Installer ایران در پایان چاپ می‌کند را روی سرور خارج اجرا کنید. شکل کلی آن:

```bash
curl -fsSL https://raw.githubusercontent.com/matin111/tp27-core/main/install.sh | bash -s -- agent https://IRAN_IP:8787 ENROLL_TOKEN --plain http://IRAN_IP:CONTROL_PORT
```

اگر HTTPS معتبر و قابل‌دسترسی باشد، Agent از HTTPS استفاده می‌کند. اگر مسیر HTTPS در دسترس نباشد، Installer می‌تواند از Signed HTTP/HMAC استفاده کند. همان لینک برای تمام Exitها استفاده می‌شود؛ فقط Controller/Token یکسان است.

بعد از Online شدن Agent، از پنل سرور خارج را انتخاب کنید و Tunnel را بسازید.

## رفتار Multi-Exit

هر Exit یک Interface و یک subnet مستقل `/30` می‌گیرد. قبل از Start هر Data Plane، TP27 اکنون Interfaceهای زنده Kernel را هم بررسی می‌کند. اگر Interface قدیمی TP27 خارج از Core باقی مانده باشد و با subnet Tunnel جدید تداخل داشته باشد، فقط همان Interface/Service یتیم TP27 پاک می‌شود؛ Interface فعال متعلق به Tunnel دیگر حذف نمی‌شود.

این Guard برای جلوگیری از باگی اضافه شده که می‌توانست دو Interface مانند نمونه زیر را هم‌زمان روی یک subnet قرار دهد:

```text
t272tunl   10.253.0.6/30
t27tunel2  10.253.0.6/30
```

TP27 به Interfaceهای غیر TP27 و سرویس‌های VPN اصلی دست نمی‌زند.

## Port Mapping

روی یک IPv4 عمومی ایران، یک `protocol + listen port` فقط می‌تواند متعلق به یک Exit باشد. بنابراین برای چند Exit، Portهای سمت ایران باید یکتا باشند.

نمونه:

```text
Exit 1: Cisco TCP/UDP 8589 -> 8589
Exit 1: OpenVPN TCP/UDP 8879 -> 8879
Exit 1: WireGuard UDP 56821 -> 56821

Exit 2: Cisco TCP/UDP 5599 -> 5599
Exit 2: OpenVPN TCP/UDP 5696 -> 5696
Exit 2: WireGuard UDP 53841 -> 53841
```

OpenVPN در TP27 به‌صورت **TCP و UDP** ساخته می‌شود. این مورد برای سرورهایی که OpenVPN فقط روی UDP Listen می‌کند ضروری است. تنظیم‌های قدیمی OpenVPN نیز در Upgrade، در صورت آزاد بودن socket سمت ایران، به حالت TCP+UDP مهاجرت می‌شوند.

L2TP/IPsec شامل UDP `500/4500/1701` است و باید روی یک Exit فعال Pin شود.

## V2Ray/Xray بدون تغییر لینک کاربران

TP27 می‌تواند Xray/V2Ray را به‌صورت Transparent از IP ایران به Inbound واقعی سرور خارج Forward کند. خود Xray روی سرور خارج تغییر نمی‌کند و UUID، Password، Flow، Path، Host، SNI/ServerName، Reality Public Key و سایر پارامترهای کاربر دست‌نخورده می‌مانند.

نمونه‌ای که در تست واقعی سالم شد:

```text
Iran public:     193.39.9.220
Exit public:     5.75.193.122
TP27 Iran:       10.253.0.10/30
TP27 Exit:       10.253.0.9/30
Xray inbound:    TCP/8080
Mapping:         Iran TCP/8080 -> 10.253.0.9:8080
```

برای این سناریو باید Health واقعی قبل از استفاده تأیید شود:

```bash
# روی ایران
ping -I t27v2ray -c 10 10.253.0.9
timeout 5 bash -c '</dev/tcp/10.253.0.9/8080'

# روی خارج
ping -I t27v2ray -c 10 10.253.0.10
timeout 5 bash -c '</dev/tcp/10.253.0.9/8080'
```

در تست معتبر، Ping هر دو سمت `0% packet loss` و TCP/8080 از داخل Tunnel `OK` بود. Xray نیز همچنان روی `*:8080` Listen می‌کرد.

اگر لینک کاربران به **Domain** اشاره می‌کند و همان Port خارجی را استفاده می‌کند، لازم نیست لینک یا Subscription کاربران تغییر کند. فقط رکورد `A` همان Domain را به IPv4 ایران تغییر دهید و Port Mapping همان Port را روی ایران به Xray خارج Forward کنید.

مثال:

```text
قبل:
example.com:8080 -> 5.75.193.122:8080

بعد، بدون تغییر لینک کاربر:
example.com:8080 -> DNS A = 193.39.9.220
                     -> TP27
                     -> 5.75.193.122:8080
```

اگر Domain رکورد `AAAA` دارد ولی IPv6 ایران برای همان سرویس آماده نیست، رکورد AAAA قدیمی را باقی نگذارید؛ در غیر این صورت بعضی Clientها ممکن است از IPv6 مستقیم به مسیر قبلی بروند و TP27 را دور بزنند.

اگر لینک کاربران مستقیماً شامل **IP خام سرور خارج** باشد، حفظ دقیق همان لینک در حالی که ورودی به IP ایران منتقل شود ممکن نیست؛ در آن حالت Address حداقل یک بار باید تغییر کند یا معماری IP/BGP جداگانه لازم است.

برای TLS/WS/Reality، چون TP27 TCP را Transparent عبور می‌دهد، SNI، Host، Path و Reality parameters نباید به‌خاطر Tunnel تغییر داده شوند.

## Health و بررسی

وضعیت Core:

```bash
tp27 status
```

Interfaceها:

```bash
ip -br -4 addr | grep -E 't27|10\.253\.'
```

سرویس‌های Data Plane:

```bash
systemctl --no-pager --plain --type=service --all | grep 'tp27-link@'
```

Port Mapping فعال:

```bash
iptables -t nat -S TP27_PRE
iptables -t nat -S TP27_POST
```

برای Health واقعی Tunnel فقط Active بودن service کافی نیست؛ Peer IP داخل Tunnel نیز باید پاسخ بدهد.

## Update / Repair

برای Upgrade سرور ایران، همان دستور نصب اولیه را دوباره اجرا کنید:

```bash
curl -fsSL https://raw.githubusercontent.com/matin111/tp27-core/main/install.sh | bash -s -- iran
```

تنظیم‌های موجود و Credential پنل حفظ می‌شوند و قبل از تغییرات مهم Backup در `/etc/tp27/backups` ساخته می‌شود.

برای Upgrade/Re-enroll سرور خارج نیز همان لینک `install.sh` با subcommand `agent` استفاده می‌شود.

## نکات ایمنی

TP27 زنجیره‌های اختصاصی iptables با نام `TP27_*` دارد و Global firewall flush انجام نمی‌دهد. Installer جدید `rp_filter` سراسری سیستم را تغییر نمی‌دهد و تنظیم لازم فقط روی Interfaceهای TP27 اعمال می‌شود. `ocserv`، OpenVPN اصلی، WireGuard اصلی، strongSwan و xl2tpd توسط Provision/Repair تونل Restart نمی‌شوند.

## فایل‌های مهم

```text
/etc/tp27/config.json                  Core state
/etc/tp27/port-maps.json               Port mappings
/etc/tp27/exits/*.json                 Per-tunnel runtime config
/etc/tp27/exit-agent.json              Exit Agent config
/etc/tp27/backups/                     Backups
/root/tp27-panel-credentials.txt       Panel credentials
/opt/tp27-panel/                       Panel runtime
/usr/local/lib/tp27/                   TP27 runtime
```
