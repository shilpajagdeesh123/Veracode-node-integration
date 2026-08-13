## 🔑 Veracode Secrets Onboarding Check — MANDATORY when Veracode-S or Veracode-R is selected

> ⛔ **DO NOT inline or duplicate any credential logic here.**
>
> Each Veracode sub-skill remains the **single source of truth** for its own onboarding, credential, scan, and remediation rules.
>
> This master skill only routes the request to the correct Veracode workflow.

When the user selects **Veracode-S** or types anything matching the Veracode scan command
(e.g. `veracode`, `start veracode`, `Veracode-S`):

### Veracode-S

1. Read the Veracode scan sub-skill:

   `.xxxl/.agents/skills/veracode-agent/SKILL.md`

2. After reading the sub-skill, **continue execution immediately**.

3. Do NOT stop merely because the sub-skill was loaded.

4. Follow the execution instructions from the Veracode sub-skill.

5. The Veracode scan workflow must ultimately submit the appropriate terminal launcher:

   **Windows**

   `cmd.exe /d /c "veracode-enterprise\runtime-veracode.bat"`

   **macOS**

   `bash veracode-enterprise/runtime-veracode.sh`

6. Once the terminal command has been successfully submitted:

   **STOP the Copilot workflow immediately.**

7. Do NOT:
   - wait for scan completion;
   - monitor terminal output;
   - poll Veracode status;
   - read `results.json`;
   - inspect `.GitHub/output`;
   - start remediation automatically.

The terminal owns the complete Veracode scan after handoff.

---

### Veracode-R

When the user selects **Veracode-R**:

1. Read:

   `.xxxl/.agents/skills/veracode-remediation/SKILL.md`

2. Continue execution according to that remediation sub-skill.

3. Only stop when the remediation sub-skill explicitly requires user input, such as:
   - selecting a project/module;
   - deciding whether Medium findings should be included;
   - deciding whether Low findings should be included.

Do NOT apply the Veracode-S terminal-stop behavior to Veracode-R unless explicitly instructed by the remediation sub-skill.

---

## Mandatory routing rule

Reading a Veracode sub-skill is **not considered completion**.

For `Veracode-S`:

`Read sub-skill → execute terminal launcher → STOP`

For `Veracode-R`:

`Read sub-skill → execute remediation workflow → stop only when actual user input is required`

Do not use:

`Read sub-skill → STOP`

unless the selected sub-skill explicitly requires an immediate user response.
