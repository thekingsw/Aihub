# XingchiAPI key rotation through AI Hub

## Scope and flow

TinkerApp is the only intended user interface. Its backend must authenticate the
session, require the owner role, and enforce CSRF protection before making a
server-to-server request to AI Hub over the private Tailscale network. The
browser must never call AI Hub directly. AI Hub exposes `POST
/internal/rotate-codex-key`, protected by its existing internal bearer token.
The request is JSON with exactly one field, `api_key`; AI Hub accepts no config
path, provider name, or validation URL from the caller.

AI Hub validates the candidate against the fixed
`https://api.xingchiapi.com/v1/models` endpoint using HTTPS certificate
verification, a bounded timeout, and no redirects. It proceeds only on HTTP
200. A 401/403 response means the candidate was rejected; other statuses,
redirects, and network/TLS failures fail closed. No inference endpoint is used,
and a models response does not prove the key has inference balance.

## File update and failure behavior

The target is the Codex CLI TOML file at the server's fixed
`~/.codex/config.toml` path. AI Hub parses the TOML, requires the
`model_providers.OpenAI` table, changes only
`experimental_bearer_token`, parses the candidate result again, and preserves
unrelated content. It detects a concurrent change before replacing the file.

Before the atomic replacement, AI Hub writes an exact byte-for-byte backup in
the same directory and flushes it to disk. The backup inherits only private
read/write permissions; the new config is written to a same-directory
temporary file with the original file mode, flushed, and atomically replaced.
If verification, parsing, backup, or writing fails, the config is not
intentionally updated and the endpoint returns failure. A backup may remain if
the subsequent atomic write fails, which preserves recovery data.

## Secret handling and audit

The endpoint never logs request bodies, authorization headers, candidate keys,
upstream bodies, or exception text. Successful responses include the candidate
key length and backup filename, but never either key. Repeating the current key
returns success with `changed: false` and no backup; it does not rewrite the
config. A changed key returns `changed: true` and its backup filename. The
metadata-only audit record contains UTC time, action, result, normalized reason,
and candidate length, not the candidate itself. The config and backup are the
only files that contain key material. The internal bearer token must remain
server-side and be provisioned through the existing internal-token mechanism.

## Access and operational risks

The internal bearer token is a privileged credential; disclosure could allow
rotation requests, so access to the AI Hub host and token provisioning must be
restricted. A compromised TinkerApp owner session or backend can request a
rotation; owner authorization and CSRF checks belong in TinkerApp. Protect the
backup directory and account permissions because backups contain the previous
key. A local administrator, compromised PC/host, filesystem failure, or
concurrent config writer can still affect the operation. Atomic replacement
prevents partial TOML writes but cannot guarantee storage hardware durability
or prevent a writer that races after the final comparison. A models HTTP 200
only confirms endpoint acceptance, not balance or inference success.

## Validation evidence and boundaries

`test_key_rotation.py` exercises the endpoint with temporary config, audit,
and backup paths. It covers rejected internal authentication, rejected keys,
network failure, backup failure, write failure, successful exact backup, and
preservation of unrelated TOML values. Tests substitute upstream validation;
they do not call XingchiAPI or modify the real Codex config. Live testing and
any real key application require separately authorized scope and verification.

## P1 acceptance — 2026-11-21

P1 chỉ kiểm chứng offline: config/audit/backup tạm trong ai-hub, HTTP server loopback và upstream giả lập bằng opener/mock; không gọi XingchiAPI thật, không đọc/ghi config thật, không restart dịch vụ. Tin giao đã duyệt kế hoạch và mở P1, dù snapshot phase current còn null; không tự sửa metadata phase.

Key đầu vào phải là ASCII hiển thị không chứa khoảng trắng (1–4096 ký tự), tránh lỗi mã hóa header. Các lỗi upstream 301/302/401/403/429/500, timeout, mất mạng đều đóng thao tác; chỉ 200 được áp dụng. Test lỗi fsync backup và os.replace config xác nhận giữ nguyên config, dọn file tạm và giữ backup hoàn chỉnh khi ghi config thất bại.

Giới hạn vận hành: chmod trên Windows không thiết lập DACL riêng; file tạm/backup kế thừa ACL thư mục. Trước P3 phải kiểm chứng ACL thư mục config chỉ cho tài khoản phù hợp. Audit là best-effort (lỗi ghi audit không rollback config), không phải nhật ký chống sửa đổi. Không cam kết giao dịch giữa audit và config. P2/P3 chưa triển khai/kiểm chứng trong lượt này.
