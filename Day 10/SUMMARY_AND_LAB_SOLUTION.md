# Day 10 - Security Mechanisms, Management, and Practice

Source: [lecture PDF](lect10_security_mechanisms.pdf), 45 pages. Page references below use PDF page numbers, not slide labels. The lab is on pages 41-45. Rendered slides and extracted text are in `../tmp/pdfs/day10/`, generated using the existing `../tmp/pdfs/day6/extract.swift` helper.

## 1. Knowledge summary

### Public keys and security management (pages 4-9)

- A public key alone does not prove identity. An attacker could publish their own key under someone else's name.
- A **certificate** binds an identity to a public key through a CA's digital signature. Verify the signature chain to a trusted root, the intended identity, validity dates, constraints, and applicable revocation information.
- **Hierarchical PKI** uses root and intermediate CAs. A **web of trust** uses peer signatures and locally chosen trust relationships.
- **CRL:** periodically downloaded, signed revocation list. It can become large and stale between updates.
- **OCSP:** asks a responder about a particular certificate. **OCSP stapling:** the server supplies a signed status response, reducing direct client queries. The response still needs signature and freshness checks.
- Key management covers secure generation, storage, distribution, rotation, revocation, and destruction. Changing a certificate alone does not repair a stolen private key.
- **Escrow** enables recovery but introduces another place where secrets must be protected.
- **Shamir (k, n) sharing:** choose a random degree `k-1` polynomial over a finite field with the secret as its constant term. Any `k` distinct shares reconstruct it by interpolation; fewer than `k` reveal no information under the scheme's assumptions.

### Secure groups and LKH (pages 11-18)

| Mechanism | Main idea | Trade-off |
| --- | --- | --- |
| Pairwise keys | A different key for each pair | `N(N-1)/2` keys; repeated encryption for multicast |
| Flat group key | Encrypt a payload once for everyone | A leave requires distributing a new key to remaining members |
| LKH | Members hold their leaf key and ancestor keys | Path-only rekeying reduces encrypted key blocks to `O(log N)` in a balanced tree |
| Iolus | Split the group into separately managed subgroups | Local membership changes; trusted intermediaries bridge groups |
| OFT | Derive tree keys using one-way functions | Different bandwidth and key-derivation trade-offs |
| GDH / TGDH | Members contribute to a shared key | Decentralized agreement needs extra coordination and computation |

For a complete `d`-ary tree, `h = log_d(N)`, and each member holds `h+1` keys including its individual key. For non-full trees use the actual path length; capacity and active membership are different quantities.

On **join**, replace ancestor keys so the entrant cannot recover earlier group traffic. Existing members can receive replacements protected by old keys; the entrant needs a separate protected bootstrap containing only its new path.

On **leave**, replace every ancestor key known to the departing member. Distribute the replacements bottom-up under keys accessible only to survivors. Never protect a replacement solely with an old key the departing member knows.

The lecture calls join protection “forward secrecy” and leave protection “backward secrecy.” These labels are reversed relative to common group-key terminology. For this lab, use the explicit properties: **new members cannot decrypt past traffic; departed members cannot decrypt future traffic**. These membership properties differ from TLS perfect forward secrecy.

An important counting distinction: an **encrypted key block** is not necessarily a network packet. Multiple blocks can be bundled into one TCP message. Sending that message to each client still incurs network delivery costs; `O(log N)` describes key-update material, not necessarily all TCP bytes.

Group encryption does not identify the original sender: every member knowing the group key can create a valid group ciphertext. **TESLA** addresses source authentication using delayed MAC-key disclosure and timing checks, with loose clock synchronization and buffered packets.

### Access control and network protection (pages 20-27)

- **RBAC:** assign users to roles and permissions to roles.
- **ABAC:** evaluate subject, object, action, and environment attributes. A publisher role by itself is insufficient if the action or permitted time is wrong.
- **Cryptographic access control:** distribute decryption keys only to authorized parties. Revocation cannot erase plaintext someone already copied.
- **Capability:** an unforgeable bearer token identifying an object and allowed operations. It is fast to present but requires a revocation strategy, such as expiry, indirection, or object-key rotation.
- The lecture's Amoeba layout is `48-bit server port + 24-bit object ID + 8-bit rights + 48-bit check = 128 bits`. A check field protects the rights against tampering. Do not assume an arbitrary hash construction supports safe client-side rights restriction.
- **DMZ:** isolate public services from the internal network and restrict connections across each boundary.
- Packet filters inspect addresses and ports; stateful firewalls track connections; application gateways inspect protocol content.
- **IDS** detects suspicious activity; **IPS** can block it. Signature detection recognizes known patterns; anomaly detection flags deviations. Neither guarantees zero false positives or detection of every attack.
- **SYN cookies** reduce state allocation for half-open TCP connections. They do not prevent bandwidth exhaustion or every form of DDoS.
- A sandbox limits what untrusted code can do. The JVM class-loader/verifier/security-manager model in the slides is historical context, not a complete modern deployment prescription.

### Example systems and design rules (pages 29-36)

- **Kerberos:** AS issues a TGT; TGS exchanges the TGT for service tickets. Authenticators, timestamp checks, and replay handling protect ticket use. The KDC and clock assumptions matter.
- **Globus GSI:** certificates provide cross-domain identity; short-lived proxy credentials support delegation without sharing the user's long-term private key. Certificates contain public keys, not private keys.
- **TLS 1.3:** authenticated encryption and ephemeral key agreement support secure channels; the slide's PFS description should not be treated as applying to every PSK mode. Early `0-RTT` data has replay risks.
- **Blind signatures:** obtain a bank signature on a blinded coin, then unblind it without revealing the coin identifier during issuance. Detecting double spending or revealing an identity requires additional protocol design; a blind signature alone does not do that.
- **Sybil:** one attacker creates many identities. **Eclipse:** malicious neighbors isolate a target from honest peers.
- Apply defense in depth, least privilege, a small trusted computing base, separate policy from enforcement, and default-deny authorization.

## 2. Worked numerical review (pages 38-39)

### RSA

Given `p=5`, `q=11`, `e=3`, plaintext `P=9`:

1. `n = 5*11 = 55`.
2. `phi(n) = (5-1)(11-1) = 40`.
3. `gcd(3,40)=1`.
4. `d=27`, because `3*27 = 81 = 1 (mod 40)`.
5. Public key: `(e,n)=(3,55)`; private exponent: `d=27`.
6. `C = 9^3 mod 55 = 729 mod 55 = 14`.
7. `14^2 mod 55=31`, `14^4 mod 55=26`, `14^8 mod 55=16`, `14^16 mod 55=36`.
8. Since `27=16+8+2+1`, `14^27 mod 55 = 36*16*31*14 mod 55 = 9`.

These deliberately small parameters illustrate arithmetic only.

### Diffie-Hellman

Given `p=23`, `g=5`, Alice's secret `a=4`, Bob's secret `b=3`:

- `A = 5^4 mod 23 = 4`.
- `B = 5^3 mod 23 = 10`.
- Alice: `K = 10^4 mod 23 = 18`.
- Bob: `K = 4^3 mod 23 = 18`.

Both derive **18**. DH still needs authentication to resist an active intermediary.

### LKH for 1,024 members

For a complete binary tree, `N=1024`, `d=2`:

- Height: `h=log2(1024)=10` edges.
- Keys per member: `h+1=11`.
- Lecture leave-count estimate: `d*h=20` encrypted blocks.
- Flat redistribution to survivors: `N-1=1023` blocks.
- Reduction under that estimate: `(1023-20)/1023 * 100 = 98.045%`.

**Exact count depends on the protocol.** A bottom-up binary leave procedure that omits the departed leaf sends one block at its parent and two at each higher level: `1+2(h-1)=2h-1`. This is **19** blocks for height 10 and **5** for height 3; `2h` is a useful upper bound. The report and demo should distinguish their actual count from the lecture's sample of 6.

For the slide's simplified expression `d*log_d(N)`, minimizing `d/ln(d)` over real `d` gives `d=e`; the best integer under that expression is 3, not 4. Different payload or implementation models can give different optima.

## 3. Conceptual review (page 40)

**Needham-Schroeder replay flaw:** if an attacker obtains an old session key and replays the corresponding old ticket, a recipient that does not check freshness can accept the attacker as the original client. Timestamped, expiring tickets reduce this risk; implementations also need appropriate clock checks and replay handling. A nonce challenge alone does not help if the attacker already knows that session key.

**ACL versus capabilities:** an ACL stores permissions with the object, making changes to that object's access list direct. A capability is held by a subject and presented to the object server. It supports delegation, but distributed copies make revocation harder without expiry or an explicit revocation mechanism.

## 4. Lab requirements and proposed solution

The requested artifact is a **Secure Group Multicast & ABAC Policy Simulator**. It must provide:

1. A binary LKH key controller with member join and leave.
2. AES-256-GCM payload encryption with the current root key.
3. TCP server and client processes, each client retaining only its own path keys.
4. Rekey blocks identifying the updated node and the child key used to protect it.
5. A policy allowing an active authenticated Publisher to perform `publish` only between 08:00 and 18:00, inclusive.
6. Console traces and tests proving past-message exclusion for entrants and future-message exclusion for departed clients.
7. Source code and a design report. The rubric weights LKH 35%, AES 30%, ABAC 25%, and structure/report 10%.

The architecture and validation plan are in [LAB_DESIGN.md](LAB_DESIGN.md). The runnable implementation is documented in [README.md](README.md), with protocol and validation details in [DESIGN_REPORT.md](DESIGN_REPORT.md). Run `python3 run_demo.py` from this folder to reproduce the process-based lab and capture `execution_trace.log`.
