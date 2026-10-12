# TP27 plain control fallback

When direct TLS or SSH control traffic is unreliable, the exit agent may use an outbound HTTP control transport over TCP/443. The enrollment token must never be sent as plaintext. Enrollment uses a one-time challenge plus HMAC proof. After enrollment, every request and response is authenticated with a per-agent HMAC key. This transport is limited to non-secret actions such as status and GRE lifecycle; private keys, passwords and other secrets must not be sent over it.
