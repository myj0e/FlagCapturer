---
name: ctf-web
description: Analyze authorized CTF web applications and saved HTTP evidence; choose probes from observed application behavior.
license: MIT
---
# ctf-web

Use `skill_read` to read the references below only when relevant. The guide is adapted for FlagCapturer; upstream examples are reference material, not executable environment setup.

## Execution in this workbench

- Execute commands and parse challenge data only through sandbox tools. Read original and derived files by asset ID; preserve output assets and execution provenance.
- Check `workflow_environment` for an applicable method or inspect the approved `tool_request` catalog before choosing capabilities. Missing dependencies use `tool_request`; do not run upstream installers, package managers or downloads in solving scripts.
- Upstream Bash/Read/Write/Edit/Skill names describe another host. Use `command_run`, `script_save`/`script_run`, `asset_read`, `workflow_search`/`workflow_read` and `skill_read` here. Bundled scripts can be staged with `skill_materialize`, then executed with sandbox tools. Skill paths are documentation paths, not sandbox paths.
- Network access is limited to currently registered tools and granted endpoints. An HTTPS reference URL is not its content. If a required source cannot be read, promptly report the missing capability and request its text or an available acquisition method. Do not replace reading a supplied solution with blind parameter search.
- Examples involving mounting, privileged devices, unrestricted sockets or host access do not confer these capabilities. Prefer existing user-space extraction methods; report unsupported prerequisites.
- Before a substantial experiment, state the tested hypothesis, parameter basis, expected observation and next decision using `claim_record`/`experiment_record` when useful. A successful script or continuous curve does not prove that its encoding matches the task.
- User hints are prioritized leads; separate them from observed facts. Failed variants reject only their tested conditions. If progress stalls, load `ctf-route-review` and revisit incomplete evidence before adding more variants.
- Submit identified results with `candidate_submit`. Finish with evidence-backed Markdown through `run_complete`; do not add platform submission, corpus-wide uniqueness checks or unrelated objectives unless the user requested them.

## References

- [Upstream category guide](../../vendor/ctf-web/SKILL.md): select techniques and supporting documents for the current evidence. Installation and host-specific commands are descriptive examples and require the mappings above.
- [sql-injection.md](../../vendor/ctf-web/sql-injection.md): SQL injection techniques: auth bypass, UNION extraction, filter bypasses, second-order SQLi, truncation, race-assisted leaks, INSERT ON DUPLICATE KEY UPDATE password overwrite, innodb_table_stats WAF bypass
- [server-side.md](../../vendor/ctf-web/server-side.md): PHP type juggling, php://filter LFI, Python str.format traversal, SSTI (Jinja2, Twig, ERB, Mako, EJS, Vue.js, Smarty), SSRF (Host header, DNS rebinding, curl redirect, unescaped-dot regex, SNI FTP smuggling, mod_vhost_alias), PHP hash_hmac NULL
- [server-side-2.md](../../vendor/ctf-web/server-side-2.md): XXE (basic, OOB, DOCX upload), XML injection via X-Forwarded-For, PHP variable variables, PHP uniqid predictable filename, sequential regex replacement bypass, command injection (newline, blocklist, sendmail CGI, multi-barcode, git CLI), GraphQL injection (introspection, batching, interpolation)
- [server-side-exec.md](../../vendor/ctf-web/server-side-exec.md): Direct code execution paths, upload-to-RCE, deserialization-adjacent execution, LaTeX injection, header and API abuses
- [server-side-exec-2.md](../../vendor/ctf-web/server-side-exec-2.md): More execution chains: SQLi fragmentation, path parser tricks, polyglot uploads, wrapper abuse, filename injection, BMP pixel webshell with filename truncation
- [server-side-deser.md](../../vendor/ctf-web/server-side-deser.md): Java/Python/PHP deserialization and race-condition playbooks, PHP SoapClient CRLF SSRF via deserialization
- [server-side-advanced.md](../../vendor/ctf-web/server-side-advanced.md): Advanced SSRF, traversal, archive, parser, framework, and modern app-server issues, Nginx alias traversal
- [server-side-advanced-2.md](../../vendor/ctf-web/server-side-advanced-2.md): Docker API SSRF, Castor/XML, Apache expression reads, parser discrepancies, Windows path tricks, rogue MySQL server file read
- [server-side-advanced-3.md](../../vendor/ctf-web/server-side-advanced-3.md): Part 3 (CSAW/35C3/ASIS/PlaidCTF 2018): WAV polyglot upload, multi-slash URL `path.startswith` bypass, Xalan XSLT `math:random()` seed guess, SoapClient `_user_agent` CRLF method smuggling, `gopher:///` no-host URL scheme bypass, SSRF credential leak via attacker-specified outbound URL
- [server-side-advanced-4.md](../../vendor/ctf-web/server-side-advanced-4.md): Part 4: WeasyPrint SSRF/file read (CVE-2024-28184), MongoDB regex/$where blind oracle, Pongo2 Go template injection, ZIP PHP webshell, basename() bypass, wget CRLF SSRF→SMTP, Gopher SSRF to MySQL blind SQLi, React Server Components Flight RCE (CVE-2025-55182), AMQP/TLS interception via sslsplit+arpspoof, CairoSVG XXE, Bazaar repo reconstruction
- [client-side.md](../../vendor/ctf-web/client-side.md): XSS, CSRF, cache poisoning, DOM tricks, admin bot abuse, request smuggling, paywall bypass
- [client-side-advanced.md](../../vendor/ctf-web/client-side-advanced.md): CSP bypasses, Unicode tricks, XSSI, CSS exfiltration, browser normalization quirks, postMessage null origin bypass
- [auth-and-access.md](../../vendor/ctf-web/auth-and-access.md): Auth/authz bypasses, hidden endpoints, IDOR, redirect chains, subdomain takeover, AI chatbot jailbreaks
- [auth-and-access-2.md](../../vendor/ctf-web/auth-and-access-2.md): Part 2 (2018-era): `std::unordered_set` bucket collision auth bypass, `nodeprep.prepare` Unicode homograph username collision, SRP A=0/A=N auth bypass, ArangoDB AQL MERGE privilege escalation
- [auth-jwt.md](../../vendor/ctf-web/auth-jwt.md): JWT/JWE manipulation, weak secrets, header injection, key confusion, replay
- [auth-infra.md](../../vendor/ctf-web/auth-infra.md): OAuth/OIDC, SAML, CORS, CI/CD secrets, IdP abuse, login poisoning
- [node-and-prototype.md](../../vendor/ctf-web/node-and-prototype.md): Prototype pollution, JS sandbox escape, Node.js attack chains
- [web3.md](../../vendor/ctf-web/web3.md): Solidity and Web3 challenge notes
- [cves.md](../../vendor/ctf-web/cves.md): CVE-driven techniques you can match against challenge banners, headers, dependency leaks, or version strings
- [field-notes.md](../../vendor/ctf-web/field-notes.md): Long-form exploit notes: quick references for SQLi, XSS, LFI, JWT, SSTI, SSRF, command injection, XXE, deserialization, race conditions, auth bypass, and multi-stage chains
- [python-requests.md](../../vendor/ctf-web/python-requests.md): Python requests toolkit: session scaffold, Burp-Intruder-like fuzzer (sync + ThreadPoolExecutor + httpx async), payload deploy from pat-reference.md wordlists, header/param spray, cookie/JWT, proxy
- [pat-reference.md](../../vendor/ctf-web/pat-reference.md): — PayloadsAllTheThings index: bulk payloads for XSS/SQLi/SSRF/SSTI/LFI/Command Injection/Upload (requires PAT clone, see Prerequisites)

## 本项目远端工具

先读取 `network_status`；新增目标用 `network_request` 请求用户批准，不反复申请已获批范围。使用 `http_session` + `web_request` 保持登录、提交表单、上传和保存响应；正文资产可 materialize 后分析。需要 JS/DOM 时使用 `browser_open` / `browser_action`，页面内 evaluate 不在宿主执行。HTTP 与浏览器用 `web_cookie_sync` 显式同步 Cookie；下载走 web_request。用 network_status 的 blocked_requests 区分缺少授权与应用响应。非标准 HTTP 使用 TCP relay 和沙箱脚本。Resume 按历史有效步骤重验当前登录/实例状态，重建进度。
