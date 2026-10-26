# Profile Validation & Upstream Evidence — Round 3 ULTRA

**Date:** 2026-10-26  
**Benchmark:** Pi Token Optimization Benchmark — Round 3 ULTRA  
**Harness:** Pi 1.4.0 | Model: `openai-codex/gpt-6-luna` | Thinking: `max` | MCP: `disabled` | Tools: `read,bash,edit,write,subagent`  

---

## 1. Upstream Repositories & Commit Hashes

### Ponytail
- **Upstream Repository:** [https://github.com/DietrichGebert/ponytail](https://github.com/DietrichGebert/ponytail)
- **Commit SHA:** `09c434ab73ca43761c5d8b0c5d341926c2fba710`
- **License:** MIT
- **Official Ultra Specification:**
  - Defined in `skills/ponytail/SKILL.md` (Level: `ultra`): *"Also question the request: before building, push back on any part the need does not justify."*
  - Implementation rules: Push back on complexity, enforce YAGNI, prefer deletion over addition, reuse codebase patterns and standard library, shortest working diff.
  - Critical invariant: Must still implement every explicit requirement; never cut validation, error handling, rollback, transactions, or tests.

### Caveman
- **Upstream Repository:** [https://github.com/JuliusBrussee/caveman](https://github.com/JuliusBrussee/caveman)
- **Commit SHA:** `9b68f7ed12b05e7efaff18e65c5a3fc2849fd0a4`
- **License:** Apache-2.0
- **Official Ultra Specification:**
  - Defined in `skills/ultracave/SKILL.md` (Ultracave): *"Caveman at maximum compression: fragments, one word when one word is enough, each fact once... All technical substance stay. Only fluff die. Then cut again. Ultracave is caveman with the grammar stripped. Payload only."*
  - Floor (Never cut): Code blocks, commands, paths, parameters, exact error messages, test evidence, negations (not/never/no), numbers, units. Do not abbreviate code identifiers or required API names.

### RTK (Rust Token Killer)
- **Binary Version:** `rtk 1.1.0` (installed at `setup/bin/rtk`)
- **Integration:** Pi Tool Call Interception via `setup/extensions/rtk.ts`
- **Rewrite Engine:** `rtk rewrite` (Rust registry)
- **Configuration:** Safe compression with raw recovery recall mode (`sqlite` storage backing `rtk recall`), retaining failure diagnostics and non-zero exit codes.

### Headroom
- **Installed Version:** `headroom 0.41.0` (isolated in `setup/venv-headroom`)
- **Architecture:** Local HTTP reverse proxy (`127.0.0.1:8787`) designed for standard OpenAI (`/v1`) and Anthropic (`/v1/messages`) APIs.
- **Support Status for Pi openai-codex:** **UNSUPPORTED** (Detailed in Section 3).

---

## 2. Profile Matrix & SHA-256 Digests

| Profile ID | Profile Name | Optimizers Enabled | Prompt Overlay Path | SHA-256 Digest |
|:---:|---|---|---|---|
| **A** | Baseline | None (All OFF) | None | `none` |
| **C** | Combined Lite | Ponytail Lite + Caveman Lite | `setup/profiles/combined-lite.md` | `214380817ea43286f66bcf4a0b63a800f18e827535de396b8e277ca8e62e46da` |
| **U1** | Ponytail Ultra | Ponytail Ultra only | `round-3/profiles/ponytail-ultra.md` | `73f57606a1b9564c9df9b5a310dba7e1cd4b08f81b1ed87c2367b808fb3d2ad0` |
| **U2** | Caveman Ultra | Caveman Ultra only (Ultracave) | `round-3/profiles/caveman-ultra.md` | `4a09c3a7c0cd934201300fce90fd57b39b32673cd240c9b3bc51b55dc2fa1d25` |
| **U3** | Combined Ultra | Ponytail Ultra + Caveman Ultra | `round-3/profiles/combined-ultra.md` | `37888e57f2217de5dde63fadb0ad148e136128ccf09a58e39836a3c7078580e7` |
| **U4** | Combined Ultra + RTK | Ponytail Ultra + Caveman Ultra + RTK | `round-3/profiles/combined-ultra.md` | `37888e57f2217de5dde63fadb0ad148e136128ccf09a58e39836a3c7078580e7` |
| **U5** | Experimental (All + Headroom) | Ponytail Ultra + Caveman Ultra + RTK + Headroom | N/A | **UNSUPPORTED** |

---

## 3. Evaluation of Profile U5 (Headroom Integration)

### Compatibility Audit
1. **Upstream Provider Contract:**
   - Pi communicates with `openai-codex` using the internal backend endpoint `https://chatgpt.com/backend-api` and protocol `openai-codex-responses` with OAuth session token authentication.
   - Authentication tokens are managed by Pi's secure keychain / `auth.json`.
2. **Headroom Capabilities & Boundaries:**
   - Headroom 0.41.0 provides an HTTP proxy supporting standard Anthropic (`ANTHROPIC_BASE_URL`) and OpenAI (`OPENAI_BASE_URL`) REST APIs.
   - Headroom does NOT provide native transport, protocol, or session handling for ChatGPT backend SSE streaming (`openai-codex-responses`).
   - Routing Pi through Headroom would require either:
     a) Changing the model provider to an OpenAI API key endpoint (violating the pinned harness requirement of `openai-codex / gpt-6-luna`), OR
     b) Performing MITM OAuth interception and manually extracting private OAuth tokens (strictly prohibited by security instructions: *"Never implement undocumented OAuth interception or manually extract private tokens"*).
3. **Verdict:**
   - **Profile U5 is marked UNSUPPORTED.**
   - Per benchmark specification ("If Headroom requires changing the upstream model, unsupported proxy interception or unsafe authentication modifications, do not activate U5. Mark U5 UNSUPPORTED with a precise reason"), Profile U5 is excluded from the official benchmark matrix, and no model calls are made under U5.

---

## 4. Invariant Preservation Guarantee

Every prompt overlay explicitly preserves critical invariants:
- **Task Completeness:** The agent must implement all explicit requirements specified in canonical task prompts (`round-2/prompts/task-3.md` and `task-4.md`).
- **Safety Boundaries:** Validation at trust boundaries, database transactions, concurrency controls, atomic replacements, and error handling must never be removed or weakened.
- **Evaluation Independence:** All workers are evaluated against unchanged public and hidden evaluators without post-hoc repairs.
